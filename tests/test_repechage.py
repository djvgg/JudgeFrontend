# SPDX-FileCopyrightText: 2026 TOP Team Combat Control
# SPDX-License-Identifier: GPL-3.0-or-later

"""End-to-end logic test for the Repechage mode ("KO-System mit doppelter
Trostrunde", bracket_type='repechage', >32 TN) in JudgeFrontend's WS-handler.

Topology: four quarter-pools A/B/C/D, only the pool winner's victims enter the
double repechage (dynamic `('plost', pool, level)` slots), two ladders, two
crossed bronze bouts (repechage winner of one half × the OTHER half's semifinal
loser), medals 1/2/3/3 (bronze = WINNER of the bronze bout), no 5th place.

Source of truth = the frozen `main._REPECHAGE_STRUCTURE`, a 1:1 copy of edv's
`_REPECHAGE_STRUCTURE` (decoded + machine-verified against the official .xls in
`edv/tests/test_repechage_form_order.py`). Driving the whole bracket with "the
lower Los always wins" must yield a clean 1/2/3/4 sweep — the same invariant the
edv decoder asserts via `realised_order`.

Drives the dispatch helpers directly against isolated in-memory SQLite — never
the real Postgres the env points at. Participant ids == Los numbers, so
`min(p1, p2)` == "lower Los wins".
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main as backend
from src.database import Base, BracketModel, FightModel

_SIZE_ROUNDS = {32: 5, 64: 6}


@pytest.fixture()
def session():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _round0_seeds(num_rounds):
    """WB round-0 (Los p1, Los p2) pairs from the frozen structure, in pos order."""
    struct = backend._REPECHAGE_STRUCTURE[num_rounds]
    seeds = {}
    for kf, (a, b) in struct.items():
        if a and a[0] == "los" and b and b[0] == "los":
            phase, rnd, pos = backend._repe_kf_to_node(num_rounds, kf)
            assert (phase, rnd) == ("wb", 0)
            seeds[pos] = (a[1], b[1])
    return [seeds[p] for p in sorted(seeds)]


def _seed_repechage(s, size, bid=50):
    s.add(BracketModel(id=bid, bracket_type="repechage", status="pending"))
    for pos, (p1, p2) in enumerate(_round0_seeds(_SIZE_ROUNDS[size])):
        s.add(FightModel(id=5000 + pos, bracket_id=bid, bracket_phase="wb",
                         round=0, pos_in_round=pos, participant1_id=p1,
                         participant2_id=p2, fight_number=pos + 1, status="pending"))
    s.commit()
    return bid


def _finish(s, fight, winner_id):
    """Mirror the WS-handler dispatch for a finished repechage fight."""
    fight.status = "finished"
    fight.winner_id = winner_id
    s.commit()
    backend._apply_repe_graph_result(s, fight)
    backend._fill_repechage_plost_slots(s, fight)
    backend._resolve_repe_byes(s)
    backend._finalize_repechage_bracket(s, fight)


def _run_lower_los_wins(s, bid):
    """Repeatedly finish any startable wb/rep bout (both slots filled) with the
    lower Los, until the bracket is complete."""
    # Mirror the production load path: materialize, replay seeded byes through the
    # graph, fill resolved pools, resolve repechage byes, finalize — all idempotent.
    backend._reconcile_repechage(s)
    for _ in range(2000):
        f = s.query(FightModel).filter(
            FightModel.bracket_id == bid,
            FightModel.bracket_phase.in_(("wb", "rep")),
            FightModel.status == "pending",
            FightModel.participant1_id.isnot(None),
            FightModel.participant2_id.isnot(None),
        ).order_by(FightModel.bracket_phase, FightModel.round, FightModel.pos_in_round).first()
        if f is None:
            return
        _finish(s, f, min(f.participant1_id, f.participant2_id))
    raise AssertionError("did not converge")


def _fight_at(s, bid, phase, rnd, pos):
    return s.query(FightModel).filter(
        FightModel.bracket_id == bid, FightModel.bracket_phase == phase,
        FightModel.round == rnd, FightModel.pos_in_round == pos).first()


# ── Materialization ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("size", [32, 64])
def test_tree_materializes_to_full_node_count(session, size):
    s = session
    bid = _seed_repechage(s, size)
    assert backend._wb_num_rounds(s, bid) == _SIZE_ROUNDS[size]
    backend._ensure_repe_tree_materialized(s)
    expected = len(backend._REPE_NODES[_SIZE_ROUNDS[size]])
    assert s.query(FightModel).filter(FightModel.bracket_id == bid).count() == expected
    assert backend._ensure_repe_tree_materialized(s) == 0   # idempotent


# ── Full run: lower Los wins → clean 1/2/3/4 sweep ───────────────────────────

@pytest.mark.parametrize("size", [32, 64])
def test_full_run_lower_los_wins_sweeps_1234(session, size):
    s = session
    bid = _seed_repechage(s, size)
    _run_lower_los_wins(s, bid)

    b = s.get(BracketModel, bid)
    assert b.status == "completed"
    assert (b.first_place, b.second_place) == (1, 2)         # final
    assert {b.third_place_1, b.third_place_2} == {3, 4}      # the two crossed bronze winners


@pytest.mark.parametrize("size", [32, 64])
def test_bronze_is_crossed(session, size):
    """Each bronze pulls the OTHER half's semifinal loser: with lower-Los-wins the
    A/B bronze faces Los 4 (C/D SF loser) and the C/D bronze faces Los 3 (A/B SF
    loser). Only the pool winner's victims ever appear in the repechage."""
    s = session
    bid = _seed_repechage(s, size)
    _run_lower_los_wins(s, bid)
    nr = _SIZE_ROUNDS[size]
    bronze_ab, bronze_cd = (_fight_at(s, bid, *n) for n in backend._REPE_BRONZE_NODES[nr])
    # The crossed semifinal loser sits in the bronze bout (4 from C/D, 3 from A/B).
    assert 4 in (bronze_ab.participant1_id, bronze_ab.participant2_id)
    assert 3 in (bronze_cd.participant1_id, bronze_cd.participant2_id)


# ── Partial draw (byes): only the deepest plost slot can go dead ─────────────

def test_partial_40_draw_completes_with_byes(session):
    """A 40-fighter draw is a 64-bracket with 24 byes. Top seeds get R0 byes, so
    some pool winners have a None deepest victim → a repechage walkover. The
    bracket must still complete with a full medal set."""
    s = session
    bid = 51
    s.add(BracketModel(id=bid, bracket_type="repechage", status="pending"))
    # 40 fighters into the 64-snake: seed positions 1..40 are real, 41..64 Freilos.
    seeds = _round0_seeds(6)
    real = set(range(1, 41))
    for pos, (p1, p2) in enumerate(seeds):
        r1, r2 = (p1 in real), (p2 in real)
        if not r1 and not r2:
            continue   # Freilos vs Freilos → edv skips (phantom)
        if r1 and r2:
            s.add(FightModel(id=5100 + pos, bracket_id=bid, bracket_phase="wb", round=0,
                             pos_in_round=pos, participant1_id=p1, participant2_id=p2,
                             fight_number=pos + 1, status="pending"))
        else:
            real_id = p1 if r1 else p2
            s.add(FightModel(id=5100 + pos, bracket_id=bid, bracket_phase="wb", round=0,
                             pos_in_round=pos, participant1_id=real_id, participant2_id=real_id,
                             fight_number=pos + 1, status="bye", winner_id=real_id))
    s.commit()

    _run_lower_los_wins(s, bid)
    b = s.get(BracketModel, bid)
    assert b.status == "completed"
    assert b.first_place == 1 and b.second_place == 2          # lower Los still sweeps the top
    assert b.third_place_1 is not None and b.third_place_2 is not None
    assert len({b.first_place, b.second_place, b.third_place_1, b.third_place_2}) == 4

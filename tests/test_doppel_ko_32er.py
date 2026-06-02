# SPDX-FileCopyrightText: 2026 TOP Team Combat Control
# SPDX-License-Identifier: GPL-3.0-or-later

"""End-to-end logic test for the 32er Doppel-KO in JudgeFrontend's WS-handler.

The 32er has a DIFFERENT medal topology than 8er/16er (partial double-elimination
with repechage cross-back): the two Trostrunde champions fold into a medal round
against the WB semifinal winners, the final pulls from THOSE winners, and bronze
are the medal-round LOSERS. JF drives it from a frozen feeder-graph
(`main._CANONICAL_32`) rather than the `_LB_STRUCTURE` lambdas. See WSP/CLAUDE.md
(Doppel-KO invariant, 32er) and the architect decision 2026-06-02-2.

`REALISED_32` and the seeds/placements below are the INDEPENDENT oracle, decoded
from the official `ko_32.xls` by `edv/tests/ko_form_decoder.py` (Merlin-confirmed,
green). If main.py's graph is mis-transcribed, these assertions catch it.

Drives the dispatch helpers directly against isolated in-memory SQLite — never
the real Postgres the env points at.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main as backend
from src.database import Base, BracketModel, FightModel


@pytest.fixture()
def session():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


# WB round-0 seeding (pos → (Los p1, Los p2)), = REALISED_32 Kf1..16.
_SEEDS = [
    (1, 17), (9, 25), (5, 21), (13, 29), (3, 19), (11, 27), (7, 23), (15, 31),
    (2, 18), (10, 26), (6, 22), (14, 30), (4, 20), (12, 28), (8, 24), (16, 32),
]

# Order actually fought when the lower Los always wins: (Kampffolge, (p1, p2)).
# Verbatim from edv/tests/test_ko_form_order.py REALISED_32 (decoded from ko_32.xls).
REALISED_32 = [
    (1, (1, 17)), (2, (9, 25)), (3, (5, 21)), (4, (13, 29)), (5, (3, 19)),
    (6, (11, 27)), (7, (7, 23)), (8, (15, 31)), (9, (2, 18)), (10, (10, 26)),
    (11, (6, 22)), (12, (14, 30)), (13, (4, 20)), (14, (12, 28)), (15, (8, 24)),
    (16, (16, 32)), (17, (1, 9)), (18, (5, 13)), (19, (3, 11)), (20, (7, 15)),
    (21, (2, 10)), (22, (6, 14)), (23, (4, 12)), (24, (8, 16)), (25, (20, 28)),
    (26, (24, 32)), (27, (18, 26)), (28, (22, 30)), (29, (19, 27)), (30, (23, 31)),
    (31, (17, 25)), (32, (21, 29)), (33, (1, 5)), (34, (3, 7)), (35, (2, 6)),
    (36, (4, 8)), (37, (20, 10)), (38, (24, 14)), (39, (18, 12)), (40, (22, 16)),
    (41, (19, 9)), (42, (23, 13)), (43, (17, 11)), (44, (21, 15)), (45, (1, 3)),
    (46, (2, 4)), (47, (10, 14)), (48, (12, 16)), (49, (9, 13)), (50, (11, 15)),
    (51, (10, 5)), (52, (12, 7)), (53, (9, 6)), (54, (11, 8)), (55, (5, 7)),
    (56, (6, 8)), (57, (5, 4)), (58, (3, 6)), (59, (1, 4)), (60, (2, 3)),
    (61, (1, 2)),
]


def _fight_at(s, bracket_id, phase, rnd, pos):
    return s.query(FightModel).filter(
        FightModel.bracket_id == bracket_id, FightModel.bracket_phase == phase,
        FightModel.round == rnd, FightModel.pos_in_round == pos).first()


def _finish32(s, fight, winner_id):
    """Mirror the WS-handler dispatch for a finished 32er fight (graph engine)."""
    fight.status = "finished"
    fight.winner_id = winner_id
    s.commit()
    backend._apply_ko_graph_result(s, fight)
    backend._finalize_doppel_ko_bracket(s, fight)


def _seed_32er(s, bid=30):
    s.add(BracketModel(id=bid, bracket_type="ko", status="pending"))
    for pos, (p1, p2) in enumerate(_SEEDS):
        s.add(FightModel(id=600 + pos, bracket_id=bid, bracket_phase="wb",
                         round=0, pos_in_round=pos, participant1_id=p1,
                         participant2_id=p2, fight_number=pos + 1, status="pending"))
    s.commit()
    return bid


def test_32er_eager_materialization():
    """The whole 32er tree (61 bouts) materializes: WB only to the semifinal
    (round 3 — no WB final), LB rounds 0..7 (r6 = medal round, r7 = final)."""
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    bid = _seed_32er(s)
    assert backend._wb_num_rounds(s, bid) == 5
    backend._ensure_ko_tree_materialized(s)

    assert s.query(FightModel).filter(FightModel.bracket_id == bid).count() == 61
    # WB stops at the semifinal — there is NO WB round 4 (the medal round replaces it).
    assert _fight_at(s, bid, "wb", 3, 0) is not None
    assert _fight_at(s, bid, "wb", 4, 0) is None
    # LB ladder runs 0..7.
    lb_rounds = {r for (r,) in s.query(FightModel.round).filter(
        FightModel.bracket_id == bid, FightModel.bracket_phase == "lb").distinct()}
    assert lb_rounds == set(range(8))
    # Idempotent: a second pass creates nothing.
    assert backend._ensure_ko_tree_materialized(s) == 0
    s.close()


def test_32er_full_run_matches_official_form(session):
    """Drive the whole 32er with 'lower Los wins' and assert every bout's pairing
    matches the official ko_32 form, plus the four medal placements."""
    s = session
    bid = _seed_32er(s)
    backend._ensure_ko_tree_materialized(s)

    for kf, (a, b) in REALISED_32:
        node = backend._kf_to_node_32(kf)
        f = _fight_at(s, bid, *node)
        assert f is not None, f"Kf{kf}: fight missing at {node}"
        assert (f.participant1_id, f.participant2_id) == (a, b), \
            f"Kf{kf}@{node}: got {(f.participant1_id, f.participant2_id)}, want {(a, b)}"
        _finish32(s, f, min(a, b))

    b = s.get(BracketModel, bid)
    assert b.status == "completed"
    assert (b.first_place, b.second_place) == (1, 2)        # final Kf61
    assert {b.third_place_1, b.third_place_2} == {3, 4}     # medal-round losers Kf59/60


def test_32er_medal_round_is_fed_cross(session):
    """The structurally-new part: SF winners meet Trostrunde champions in the
    medal round (lb r6), and the final (lb r7) pulls from those — not the SF."""
    s = session
    bid = _seed_32er(s)
    backend._ensure_ko_tree_materialized(s)
    for kf, (a, b) in REALISED_32:
        _finish32(s, _fight_at(s, bid, *backend._kf_to_node_32(kf)), min(a, b))

    medal0 = _fight_at(s, bid, "lb", 6, 0)   # Kf59 = winner(SF Kf45) vs winner(TR Kf57)
    assert (medal0.participant1_id, medal0.participant2_id) == (1, 4)
    final = _fight_at(s, bid, "lb", 7, 0)    # Kf61 = winners of the medal round
    assert (final.participant1_id, final.participant2_id) == (1, 2)


def test_32er_next_match_follows_graph(session):
    """nextMatchId/-Pos (which drive renderKoTree's topology) must follow the real
    winner edge, NOT the binary tree: WB semifinals feed the medal round (lb r6),
    the medal round feeds the final, and the LB advance is pos-preserving. With the
    binary-tree formula the SF would point at a nonexistent wb r4 and the tree would
    render the medal round/final as disconnected roots."""
    s = session
    bid = _seed_32er(s)
    backend._ensure_ko_tree_materialized(s)
    fights = s.query(FightModel).filter(FightModel.bracket_id == bid).all()
    fl = {(f.bracket_id, f.bracket_phase, f.round, f.pos_in_round): f.id for f in fights}
    nrl = {bid: 5}
    id_to_node = {f.id: (f.bracket_phase, f.round, f.pos_in_round) for f in fights}

    def next_of(node):
        # group_lookup={} skips _resolve_groups' raw-SQL IN (SQLite-incompatible);
        # production passes a real one. We only assert nextMatch wiring here.
        d = backend._build_match_dict(s, _fight_at(s, bid, *node), fl, {}, nrl)
        nid = d["nextMatchId"]
        return (id_to_node.get(nid), d["nextMatchPos"]) if nid else (None, None)

    assert next_of(("wb", 3, 0)) == (("lb", 6, 0), "p1")   # SF Kf45 → medal Kf59 slot1
    assert next_of(("wb", 3, 1)) == (("lb", 6, 1), "p1")   # SF Kf46 → medal Kf60 slot1
    assert next_of(("lb", 5, 0)) == (("lb", 6, 0), "p2")   # TR-SF Kf57 → medal Kf59 slot2
    assert next_of(("lb", 5, 1)) == (("lb", 6, 1), "p2")   # TR-SF Kf58 → medal Kf60 slot2
    assert next_of(("lb", 6, 0)) == (("lb", 7, 0), "p1")   # medal → final
    assert next_of(("lb", 6, 1)) == (("lb", 7, 0), "p2")
    assert next_of(("lb", 7, 0)) == (None, None)           # final has no onward edge
    assert next_of(("lb", 0, 1)) == (("lb", 1, 1), "p1")   # LB advance pos-preserving
    assert next_of(("wb", 0, 1)) == (("wb", 1, 0), "p2")   # WB still the binary tree


def test_over_32_stays_graceful(session):
    """A 64er (num_rounds==6) is not wired: a WB-r0 finish neither drops to LB nor
    crashes — graceful no-op, consistent with the pre-32er stub behaviour."""
    s = session
    s.add(BracketModel(id=40, bracket_type="ko", status="pending"))
    for pos in range(32):  # 32 wb round-0 fights → 64er
        s.add(FightModel(id=700 + pos, bracket_id=40, bracket_phase="wb", round=0,
                         pos_in_round=pos, participant1_id=2 * pos + 1,
                         participant2_id=2 * pos + 2, fight_number=pos + 1, status="pending"))
    s.commit()
    assert backend._wb_num_rounds(s, 40) == 6
    f = _fight_at(s, 40, "wb", 0, 0)
    f.status, f.winner_id = "finished", 1
    s.commit()
    # Not the 32er graph (no consumers) and no _LB_STRUCTURE[6] → no LB seeding.
    assert backend._apply_ko_graph_result(s, f) == []
    assert backend._drop_loser_to_lb(s, f) is None
    assert s.query(FightModel).filter(
        FightModel.bracket_id == 40, FightModel.bracket_phase == "lb").count() == 0

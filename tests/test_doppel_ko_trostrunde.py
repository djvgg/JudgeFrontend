# SPDX-FileCopyrightText: 2026 TOP Team Combat Control
# SPDX-License-Identifier: GPL-3.0-or-later

"""End-to-end logic test for the Doppel-KO Trostrunde (Loser-Bracket), 8er.

Drives the WS-handler helpers directly against an in-memory SQLite DB:
    _propagate_winner (now lazy-creates wb rounds), _drop_loser_to_lb,
    _advance_lb_winner, _finalize_doppel_ko_bracket.

Decision 2026-06-01 (/wsp-architect): modified judo Trostrunde, half-based,
2 bronze. See WSP/CLAUDE.md.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main as backend
from src.database import Base, BracketModel, FightModel


@pytest.fixture()
def session():
    # Isolated in-memory DB — never touch the real Postgres the env points at.
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _fight_at(s, bracket_id, phase, rnd, pos):
    return s.query(FightModel).filter(
        FightModel.bracket_id == bracket_id, FightModel.bracket_phase == phase,
        FightModel.round == rnd, FightModel.pos_in_round == pos).first()


def _finish(s, fight, winner_id):
    """Mirror the WS-handler dispatch for a finished fight."""
    fight.status = "finished"
    fight.winner_id = winner_id
    s.commit()
    if fight.bracket_phase == "wb":
        propagated = backend._propagate_winner(s, fight)
        backend._drop_loser_to_lb(s, fight)
        if propagated is None:
            backend._finalize_doppel_ko_bracket(s, fight)
    elif fight.bracket_phase == "lb":
        advanced = backend._advance_lb_winner(s, fight)
        if advanced is None:
            backend._finalize_doppel_ko_bracket(s, fight)


def _seed_8er(s):
    bid = 10
    s.add(BracketModel(id=bid, bracket_type="ko", status="pending"))
    # wb round 0: 4 fights, seeds gp 1..8 (winner = lower gp in each, by score)
    for pos, (p1, p2) in enumerate([(1, 2), (3, 4), (5, 6), (7, 8)]):
        s.add(FightModel(id=100 + pos, bracket_id=bid, bracket_phase="wb",
                         round=0, pos_in_round=pos, participant1_id=p1,
                         participant2_id=p2, fight_number=pos + 1, status="pending"))
    s.commit()
    return bid


def test_8er_trostrunde_end_to_end(session):
    s = session
    bid = _seed_8er(s)

    # Round 0: p1 wins each → winners 1,3,5,7 advance; losers 2,4,6,8 drop.
    for pos, winner in enumerate([1, 3, 5, 7]):
        _finish(s, _fight_at(s, bid, "wb", 0, pos), winner)

    # WB semifinals lazy-created and seeded.
    sf0, sf1 = _fight_at(s, bid, "wb", 1, 0), _fight_at(s, bid, "wb", 1, 1)
    assert (sf0.participant1_id, sf0.participant2_id) == (1, 3)
    assert (sf1.participant1_id, sf1.participant2_id) == (5, 7)
    # LB round 0 seeded from the half's R0 losers.
    lb0, lb1 = _fight_at(s, bid, "lb", 0, 0), _fight_at(s, bid, "lb", 0, 1)
    assert (lb0.participant1_id, lb0.participant2_id) == (2, 4)
    assert (lb1.participant1_id, lb1.participant2_id) == (6, 8)

    # Semifinals: p1 wins (1, 5) → final; SF losers 3, 7 → bronze matches CROSSED
    # (ko_8.xls: SF pos p loser → bronze pos 1−p, slot p2): SF0 loser 3 → bz1, SF1 loser 7 → bz0.
    _finish(s, sf0, 1)
    _finish(s, sf1, 5)
    final = _fight_at(s, bid, "wb", 2, 0)
    assert (final.participant1_id, final.participant2_id) == (1, 5)
    bz0, bz1 = _fight_at(s, bid, "lb", 1, 0), _fight_at(s, bid, "lb", 1, 1)
    assert bz0.participant2_id == 7 and bz1.participant2_id == 3

    # LB round 0: winners 2, 6 advance into the bronze matches (slot p1, pos-preserving).
    _finish(s, lb0, 2)
    _finish(s, lb1, 6)
    bz0, bz1 = _fight_at(s, bid, "lb", 1, 0), _fight_at(s, bid, "lb", 1, 1)
    assert (bz0.participant1_id, bz0.participant2_id) == (2, 7)
    assert (bz1.participant1_id, bz1.participant2_id) == (6, 3)

    # Final + both bronze → bracket completed with 4 placements.
    _finish(s, final, 1)            # 1st=1, 2nd=5 (not yet completed: bronze open)
    assert s.get(BracketModel, bid).status != "completed"
    _finish(s, bz0, 2)             # 3rd place
    _finish(s, bz1, 6)             # 3rd place → finalize
    b = s.get(BracketModel, bid)
    assert b.status == "completed"
    assert (b.first_place, b.second_place) == (1, 5)
    assert {b.third_place_1, b.third_place_2} == {2, 6}


def test_loser_drop_skips_bye(session):
    """A bye (p1==p2) has no real loser to drop."""
    s = session
    s.add(BracketModel(id=11, bracket_type="ko", status="pending"))
    for pos in range(4):
        s.add(FightModel(id=200 + pos, bracket_id=11, bracket_phase="wb", round=0,
                         pos_in_round=pos, participant1_id=9, participant2_id=9,
                         fight_number=pos + 1, status="pending"))
    s.commit()
    bye = _fight_at(s, 11, "wb", 0, 0)
    bye.status, bye.winner_id = "finished", 9
    s.commit()
    assert backend._drop_loser_to_lb(s, bye) is None
    assert s.query(FightModel).filter(FightModel.bracket_phase == "lb").count() == 0


def _seed_8er_with_byes(s, bid=12):
    """8er (4 round-0 fights) with three Freilose, two of them adjacent (pos 2+3).

    A Freilos is stored as a self-pairing (p1==p2) with status 'bye' and the present
    fighter already set as winner_id — exactly how edv seeds it.
    """
    s.add(BracketModel(id=bid, bracket_type="ko", status="pending"))
    rows = [
        (0, 1, 2, None, "pending"),   # real fight
        (1, 3, 3, 3, "bye"),          # Freilos → r1 p0, slot p2
        (2, 5, 5, 5, "bye"),          # Freilos → r1 p1, slot p1  ┐ adjacent: meet in r1 p1
        (3, 7, 7, 7, "bye"),          # Freilos → r1 p1, slot p2  ┘
    ]
    for pos, p1, p2, w, st in rows:
        s.add(FightModel(id=300 + pos, bracket_id=bid, bracket_phase="wb", round=0,
                         pos_in_round=pos, participant1_id=p1, participant2_id=p2,
                         winner_id=w, fight_number=pos + 1, status=st))
    s.commit()
    return bid


def test_resolve_pending_byes_propagates_freilos(session):
    """Freilos winners reach round 1 without ever passing the finished-handler,
    two adjacent Freilose meet in the same round-1 fight, and no LB drop happens."""
    s = session
    bid = _seed_8er_with_byes(s)

    resolved = backend._resolve_pending_byes(s)
    assert resolved == 3

    # r1 p0: real fight (pos 0) not played yet → slot p1 empty, Freilos 3 in slot p2.
    sf0 = _fight_at(s, bid, "wb", 1, 0)
    assert (sf0.participant1_id, sf0.participant2_id) == (None, 3)
    # r1 p1: two adjacent Freilose (5, 7) meet — both slots filled, fight is startable.
    sf1 = _fight_at(s, bid, "wb", 1, 1)
    assert (sf1.participant1_id, sf1.participant2_id) == (5, 7)

    # A Freilos has no real loser → nothing dropped into the Trostrunde.
    assert s.query(FightModel).filter(FightModel.bracket_phase == "lb").count() == 0
    # The byes themselves stay byes.
    assert all(_fight_at(s, bid, "wb", 0, p).status == "bye" for p in (1, 2, 3))


def test_resolve_pending_byes_idempotent(session):
    """Re-running the resolver creates no duplicate follow-up fights and is a no-op."""
    s = session
    bid = _seed_8er_with_byes(s)

    backend._resolve_pending_byes(s)
    first = s.query(FightModel).filter(
        FightModel.bracket_id == bid, FightModel.bracket_phase == "wb",
        FightModel.round == 1).count()
    # Second pass: every target slot already holds its winner → nothing propagated.
    assert backend._resolve_pending_byes(s) == 0
    second = s.query(FightModel).filter(
        FightModel.bracket_id == bid, FightModel.bracket_phase == "wb",
        FightModel.round == 1).count()
    assert first == second == 2


def _seed_wb_round0(s, bid, n_fights, base_id):
    """Seed only wb round 0 with n_fights real fights (edv's job), bracket_type='ko'."""
    s.add(BracketModel(id=bid, bracket_type="ko", status="pending"))
    for pos in range(n_fights):
        s.add(FightModel(id=base_id + pos, bracket_id=bid, bracket_phase="wb", round=0,
                         pos_in_round=pos, participant1_id=2 * pos + 1,
                         participant2_id=2 * pos + 2, fight_number=pos + 1,
                         table_id=3, status="pending"))
    s.commit()
    return bid


def _phase_positions(s, bid, phase, rnd):
    return sorted(f.pos_in_round for f in s.query(FightModel).filter(
        FightModel.bracket_id == bid, FightModel.bracket_phase == phase,
        FightModel.round == rnd).all())


def test_materialize_8er_full_tree(session):
    """8er: eager-create WB r1+r2 + lb r0+r1(Bronze) as TBD, table_id inherited."""
    s = session
    bid = _seed_wb_round0(s, 30, 4, 700)   # 4 round-0 fights → 8er (R=3)
    created = backend._ensure_ko_tree_materialized(s)
    # WB: r1 (2) + r2 (1) = 3 new; LB: r0 (2) + r1 (2) = 4 new → 7.
    assert created == 7
    assert _phase_positions(s, bid, "wb", 1) == [0, 1]
    assert _phase_positions(s, bid, "wb", 2) == [0]
    assert _phase_positions(s, bid, "lb", 0) == [0, 1]
    assert _phase_positions(s, bid, "lb", 1) == [0, 1]   # Bronze
    # All eager rows are TBD with the mat (table_id=3) inherited from round 0.
    eager = s.query(FightModel).filter(
        FightModel.bracket_id == bid,
        ~((FightModel.bracket_phase == "wb") & (FightModel.round == 0))).all()
    assert all(f.participant1_id is None and f.participant2_id is None for f in eager)
    assert all(f.table_id == 3 and f.status == "pending" for f in eager)


def test_materialize_idempotent(session):
    s = session
    _seed_wb_round0(s, 31, 4, 800)
    backend._ensure_ko_tree_materialized(s)
    assert backend._ensure_ko_tree_materialized(s) == 0   # second pass: nothing new


def test_materialize_16er_full_tree(session):
    s = session
    bid = _seed_wb_round0(s, 32, 8, 900)   # 8 round-0 fights → 16er (R=4)
    created = backend._ensure_ko_tree_materialized(s)
    # WB: r1(4)+r2(2)+r3(1)=7; LB: r0(4)+r1(4)+r2(2)+r3(2)=12 → 19.
    assert created == 19
    assert _phase_positions(s, bid, "wb", 3) == [0]           # final
    assert _phase_positions(s, bid, "lb", 0) == [0, 1, 2, 3]
    assert _phase_positions(s, bid, "lb", 3) == [0, 1]        # 2 Bronze


def test_materialize_32er_full_graph(session):
    """32er (R=5) is now wired via the frozen feeder-graph: WB only to the
    semifinal (no WB round 4 — the medal round replaces it), full LB rounds 0..7.
    Full pairing/placement coverage lives in test_doppel_ko_32er.py."""
    s = session
    bid = _seed_wb_round0(s, 33, 16, 1000)   # 16 round-0 fights → 32er (R=5)
    backend._ensure_ko_tree_materialized(s)
    assert s.query(FightModel).filter(FightModel.bracket_id == bid).count() == 61
    assert _phase_positions(s, bid, "wb", 3) == [0, 1]   # semifinal = last WB round
    assert _fight_at(s, bid, "wb", 4, 0) is None          # no WB final
    assert _phase_positions(s, bid, "lb", 6) == [0, 1]   # medal round
    assert _phase_positions(s, bid, "lb", 7) == [0]      # final


def test_materialize_then_byes_coexist(session):
    """Materialize first, then resolve byes: the bye winner fills the pre-existing
    WB r1 slot (no duplicate fight)."""
    s = session
    bid = _seed_8er_with_byes(s, 34)   # wb r0: real + 3 Freilose
    backend._ensure_ko_tree_materialized(s)
    backend._resolve_pending_byes(s)
    # r1 fights were pre-created by materialization; byes filled their slots.
    assert _phase_positions(s, bid, "wb", 1) == [0, 1]
    sf1 = _fight_at(s, bid, "wb", 1, 1)
    assert (sf1.participant1_id, sf1.participant2_id) == (5, 7)   # two adjacent byes meet
    # Exactly two wb-r1 fights (no duplicates from create + propagate).
    assert s.query(FightModel).filter(
        FightModel.bracket_id == bid, FightModel.bracket_phase == "wb",
        FightModel.round == 1).count() == 2


def _fight_lookup(s, bid):
    return {(f.bracket_id, f.bracket_phase, f.round, f.pos_in_round): f.id
            for f in s.query(FightModel).filter(FightModel.bracket_id == bid).all()}


def test_slot_sources_8er(session):
    """WB-Finale ← Sieger der beiden SF; Bronze-Slots ← Sieger aus LB + Verlierer
    aus dem (gekreuzten) WB-SF."""
    s = session
    bid = _seed_wb_round0(s, 40, 4, 1100)
    backend._ensure_ko_tree_materialized(s)
    fl = _fight_lookup(s, bid)
    src = backend._compute_slot_sources(s, fl)

    final = src[(bid, "wb", 2, 0)]
    assert final[1] == {"kind": "winner", "fightId": fl[(bid, "wb", 1, 0)]}
    assert final[2] == {"kind": "winner", "fightId": fl[(bid, "wb", 1, 1)]}

    # _LB_STRUCTURE[3]: lb_advance[0] p→(1,p,1) Sieger lb r0; wb_drop[1] p→(1,1-p,2) Verlierer SF (CROSS)
    bz0 = src[(bid, "lb", 1, 0)]
    assert bz0[1] == {"kind": "winner", "fightId": fl[(bid, "lb", 0, 0)]}
    assert bz0[2] == {"kind": "loser", "fightId": fl[(bid, "wb", 1, 1)]}   # cross: 1-0=1


def test_slot_sources_16er_bronze(session):
    s = session
    bid = _seed_wb_round0(s, 41, 8, 1200)
    backend._ensure_ko_tree_materialized(s)
    fl = _fight_lookup(s, bid)
    src = backend._compute_slot_sources(s, fl)
    # _LB_STRUCTURE[4]: Bronze = lb r3. lb_advance[2] p→(3,p,1) Sieger lb r2;
    # wb_drop[2] p→(3,p,2) Verlierer SF (kein Cross auf dieser Stufe).
    bz0 = src[(bid, "lb", 3, 0)]
    assert bz0[1] == {"kind": "winner", "fightId": fl[(bid, "lb", 2, 0)]}
    assert bz0[2] == {"kind": "loser", "fightId": fl[(bid, "wb", 2, 0)]}


def test_slot_sources_32er_medal_round(session):
    """32er is wired via _KO32_CONSUMERS → sources cover the feedback edges:
    final (Kf61) ← the two medal-round winners; medal Kf59 ← HF winner + TR champ."""
    s = session
    bid = _seed_wb_round0(s, 42, 16, 1300)
    backend._ensure_ko_tree_materialized(s)
    fl = _fight_lookup(s, bid)
    src = backend._compute_slot_sources(s, fl)
    # Finale (lb r7 pos0 = Kf61) ← Sieger der zwei Medaillen-Round-Fights (Kf59/60).
    final = src[(bid, "lb", 7, 0)]
    assert final[1] == {"kind": "winner", "fightId": fl[(bid, "lb", 6, 0)]}
    assert final[2] == {"kind": "winner", "fightId": fl[(bid, "lb", 6, 1)]}
    # Medaillen-Round Kf59 (lb r6 pos0) ← Sieger HF (Kf45=wb r3 p0) + Sieger TR (Kf57=lb r5 p0).
    medal = src[(bid, "lb", 6, 0)]
    assert medal[1] == {"kind": "winner", "fightId": fl[(bid, "wb", 3, 0)]}
    assert medal[2] == {"kind": "winner", "fightId": fl[(bid, "lb", 5, 0)]}


def test_slot_sources_double_pool_final(session):
    """Doppelpool-KO-Stage: das Finale (wb r2 p0) zieht aus den HF-Siegern, damit
    der Baum 'Sieger aus #HF1/#HF2' statt 'TBD' zeigt. HFs selbst haben reale
    Pool-Platzierungen (kein TBD). Quelle = Binaerbaum-Forward-Kante."""
    s = session
    bid = 70
    s.add(BracketModel(id=bid, bracket_type="double", status="pending"))
    # KO-Stage wie _initialize_double_pool_ko_stage: HF1 (A1 vs B2), HF2 (A2 vs B1),
    # Finale (TBD vs TBD).
    s.add(FightModel(id=700, bracket_id=bid, bracket_phase="wb", round=1,
                     pos_in_round=0, participant1_id=1, participant2_id=4,
                     fight_number=10, status="pending"))
    s.add(FightModel(id=701, bracket_id=bid, bracket_phase="wb", round=1,
                     pos_in_round=1, participant1_id=2, participant2_id=3,
                     fight_number=11, status="pending"))
    s.add(FightModel(id=702, bracket_id=bid, bracket_phase="wb", round=2,
                     pos_in_round=0, participant1_id=None, participant2_id=None,
                     fight_number=12, status="pending"))
    s.commit()

    src = backend._compute_slot_sources(s, _fight_lookup(s, bid))
    final = src[(bid, "wb", 2, 0)]
    assert final[1] == {"kind": "winner", "fightId": 700}
    assert final[2] == {"kind": "winner", "fightId": 701}


def test_stage_label():
    """Endkampf-Label fuer Baum-Badge + Kampfliste: Finale / Kampf um Platz 3."""
    sl = backend._stage_label
    # 8er (num_rounds=3): WB-Finale wb r2, Bronze lb r1.
    assert sl("ko", 3, "wb", 2, 0) == "Finale"
    assert sl("ko", 3, "lb", 1, 0) == "Kampf um Platz 3"
    assert sl("ko", 3, "lb", 1, 1) == "Kampf um Platz 3"
    # 16er (num_rounds=4): WB-Finale wb r3, Bronze lb r3.
    assert sl("ko", 4, "wb", 3, 0) == "Finale"
    assert sl("ko", 4, "lb", 3, 0) == "Kampf um Platz 3"
    # Doppelpool: nur Finale (wb r2 p0); kein Bronze-Match.
    assert sl("double", 0, "wb", 2, 0) == "Finale"
    assert sl("double", 0, "wb", 1, 0) is None
    # 32er: nur Finale (lb r7 p0); keine dedizierten Bronze-Matches.
    assert sl("ko", 5, "lb", 7, 0) == "Finale"
    assert sl("ko", 5, "lb", 6, 0) is None
    # Normale Kämpfe / Pools: kein Label.
    assert sl("ko", 4, "wb", 0, 0) is None
    assert sl("ko", 4, "lb", 0, 0) is None
    assert sl("pools", None, "pool", 0, 0) is None


def _win_p1(s, f):
    """Finish a fight with its participant1 as winner (drives the dispatch helpers)."""
    _finish(s, f, f.participant1_id)


def _seed_16er(s, bid=20):
    s.add(BracketModel(id=bid, bracket_type="ko", status="pending"))
    pairs = [(1, 2), (3, 4), (5, 6), (7, 8), (9, 10), (11, 12), (13, 14), (15, 16)]
    for pos, (p1, p2) in enumerate(pairs):
        s.add(FightModel(id=400 + pos, bracket_id=bid, bracket_phase="wb", round=0,
                         pos_in_round=pos, participant1_id=p1, participant2_id=p2,
                         fight_number=pos + 1, status="pending"))
    s.commit()
    return bid


def test_16er_trostrunde_end_to_end(session):
    """16er Doppel-KO with the cross at the QF→lb-r1 entry (p^2 half-swap),
    decoded from ko_16.xls. p1 wins every bout."""
    s = session
    bid = _seed_16er(s)
    assert backend._wb_num_rounds(s, bid) == 4

    # WB round 0 (8 fights): winners 1,3,5,7,9,11,13,15; losers seed lb r0.
    for pos in range(8):
        _win_p1(s, _fight_at(s, bid, "wb", 0, pos))
    # QF lazy-created; lb r0 = R0 losers paired within draw-adjacent pairs.
    assert (_fight_at(s, bid, "wb", 1, 0).participant1_id,
            _fight_at(s, bid, "wb", 1, 0).participant2_id) == (1, 3)
    for pos, exp in enumerate([(2, 4), (6, 8), (10, 12), (14, 16)]):
        lb = _fight_at(s, bid, "lb", 0, pos)
        assert (lb.participant1_id, lb.participant2_id) == exp

    # WB quarterfinals: winners 1,5,9,13; losers 3,7,11,15 → lb r1 slot p2, CROSSED p^2.
    for pos in range(4):
        _win_p1(s, _fight_at(s, bid, "wb", 1, pos))
    assert (_fight_at(s, bid, "wb", 2, 0).participant1_id,
            _fight_at(s, bid, "wb", 2, 0).participant2_id) == (1, 5)  # SF0
    # QF loser pos p → lb r1 pos p^2: 0→2, 1→3, 2→0, 3→1
    assert _fight_at(s, bid, "lb", 1, 2).participant2_id == 3
    assert _fight_at(s, bid, "lb", 1, 3).participant2_id == 7
    assert _fight_at(s, bid, "lb", 1, 0).participant2_id == 11
    assert _fight_at(s, bid, "lb", 1, 1).participant2_id == 15

    # lb r0 winners 2,6,10,14 advance pos-preserving into lb r1 slot p1.
    for pos in range(4):
        _win_p1(s, _fight_at(s, bid, "lb", 0, pos))
    for pos, exp in enumerate([(2, 11), (6, 15), (10, 3), (14, 7)]):
        lb = _fight_at(s, bid, "lb", 1, pos)
        assert (lb.participant1_id, lb.participant2_id) == exp

    # lb r1 winners 2,6,10,14 merge (pos//2) into lb r2.
    for pos in range(4):
        _win_p1(s, _fight_at(s, bid, "lb", 1, pos))
    assert (_fight_at(s, bid, "lb", 2, 0).participant1_id,
            _fight_at(s, bid, "lb", 2, 0).participant2_id) == (2, 6)
    assert (_fight_at(s, bid, "lb", 2, 1).participant1_id,
            _fight_at(s, bid, "lb", 2, 1).participant2_id) == (10, 14)

    # WB semifinals: winners 1,9 → final; losers 5,13 → bronze (lb r3) slot p2, no cross.
    for pos in range(2):
        _win_p1(s, _fight_at(s, bid, "wb", 2, pos))
    assert (_fight_at(s, bid, "wb", 3, 0).participant1_id,
            _fight_at(s, bid, "wb", 3, 0).participant2_id) == (1, 9)
    assert _fight_at(s, bid, "lb", 3, 0).participant2_id == 5
    assert _fight_at(s, bid, "lb", 3, 1).participant2_id == 13

    # lb r2 winners 2,10 advance into the bronze matches slot p1.
    for pos in range(2):
        _win_p1(s, _fight_at(s, bid, "lb", 2, pos))
    for pos, exp in enumerate([(2, 5), (10, 13)]):
        bz = _fight_at(s, bid, "lb", 3, pos)
        assert (bz.participant1_id, bz.participant2_id) == exp

    # Final + both bronze → completed, 4 placements.
    _win_p1(s, _fight_at(s, bid, "wb", 3, 0))   # 1st=1, 2nd=9
    assert s.get(BracketModel, bid).status != "completed"
    _win_p1(s, _fight_at(s, bid, "lb", 3, 0))   # 3rd
    _win_p1(s, _fight_at(s, bid, "lb", 3, 1))   # 3rd → finalize
    b = s.get(BracketModel, bid)
    assert b.status == "completed"
    assert (b.first_place, b.second_place) == (1, 9)
    assert {b.third_place_1, b.third_place_2} == {2, 10}


def test_16er_lb_next_match_follows_lb_structure(session):
    """nextMatchId/-Pos for the LB must follow `_LB_STRUCTURE` lb_advance, NOT the
    binary tree: lb r0 advances pos-PRESERVING (binary-tree pos//2 would merge the
    wrong pairs), the merge is at lb r1→r2, and bronze (lb r3) is terminal. Without
    this renderKoTree mis-wires the Trostrunde (one bronze gets both lb-r2 feeders)."""
    s = session
    bid = _seed_16er(s)
    backend._ensure_ko_tree_materialized(s)
    fights = s.query(FightModel).filter(FightModel.bracket_id == bid).all()
    fl = {(f.bracket_id, f.bracket_phase, f.round, f.pos_in_round): f.id for f in fights}
    id_node = {f.id: (f.bracket_phase, f.round, f.pos_in_round) for f in fights}

    def next_of(node):
        # group_lookup={} skips _resolve_groups' raw-SQL IN (SQLite-incompatible).
        d = backend._build_match_dict(s, _fight_at(s, bid, *node), fl, {}, {bid: 4})
        nid = d["nextMatchId"]
        return (id_node.get(nid), d["nextMatchPos"]) if nid else (None, None)

    # lb r0 → lb r1 pos-preserving (the previously-wrong edges).
    assert next_of(("lb", 0, 1)) == (("lb", 1, 1), "p1")
    assert next_of(("lb", 0, 3)) == (("lb", 1, 3), "p1")
    # lb r1 → lb r2 merge (pos//2).
    assert next_of(("lb", 1, 1)) == (("lb", 2, 0), "p2")
    # lb r2 → bronze pos-preserving; the second bronze got the wrong feeder before.
    assert next_of(("lb", 2, 1)) == (("lb", 3, 1), "p1")
    # Bronze is terminal — no onward edge.
    assert next_of(("lb", 3, 0)) == (None, None)
    assert next_of(("lb", 3, 1)) == (None, None)
    # WB still the binary tree.
    assert next_of(("wb", 0, 2)) == (("wb", 1, 1), "p1")


def test_32er_uses_graph_not_lambda_drop(session):
    """The 32er is wired via the frozen feeder-graph (_apply_ko_graph_result), NOT
    the 8er/16er lambda path: _drop_loser_to_lb must no-op for it (there is no
    _LB_STRUCTURE[5]) so a 32er fight is never double-processed by both paths.
    Full 32er coverage lives in test_doppel_ko_32er.py."""
    s = session
    s.add(BracketModel(id=13, bracket_type="ko", status="pending"))
    for pos in range(16):  # 16 round-0 fights → 32er
        s.add(FightModel(id=500 + pos, bracket_id=13, bracket_phase="wb", round=0,
                         pos_in_round=pos, participant1_id=2 * pos + 1,
                         participant2_id=2 * pos + 2, fight_number=pos + 1, status="pending"))
    s.commit()
    f = _fight_at(s, 13, "wb", 0, 0)
    f.status, f.winner_id = "finished", 1
    s.commit()
    assert backend._wb_num_rounds(s, 13) == 5
    assert backend._drop_loser_to_lb(s, f) is None          # lambda path no-ops
    assert 5 not in backend._LB_STRUCTURE                    # 32er deliberately not a lambda struct


# --- LB-Freilos-Auflösung (WB-Bye → toter LB-Slot → Walkover/Kaskade) ---

def _seed_8er_custom(s, bid, rows, table_id=2, base=600):
    """rows: list of (pos, p1, p2, winner_or_None, status)."""
    s.add(BracketModel(id=bid, bracket_type="ko", status="pending"))
    for pos, p1, p2, w, st in rows:
        s.add(FightModel(id=base + pos, bracket_id=bid, bracket_phase="wb", round=0,
                         pos_in_round=pos, participant1_id=p1, participant2_id=p2,
                         winner_id=w, fight_number=pos + 1, table_id=table_id, status=st))
    s.commit()
    return bid


def test_lb_bye_one_dead_walkover(session):
    """WB-Freilos macht lb r0 p0 slot1 tot; der reale Geschwister-Verlierer rückt
    als Walkover-Freilos vor — der LB-Kampf kommt überhaupt erst zustande."""
    s = session
    bid = _seed_8er_custom(s, 50, [
        (0, 100, 100, 100, "bye"),       # Freilos
        (1, 101, 102, None, "pending"),  # real
        (2, 103, 104, None, "pending"),
        (3, 105, 106, None, "pending"),
    ])
    backend._ensure_ko_tree_materialized(s)
    backend._resolve_pending_byes(s)
    # Realen Geschwister-Verlierer (102) in lb r0 p0 slot2 droppen.
    f1 = _fight_at(s, bid, "wb", 0, 1)
    f1.status, f1.winner_id = "finished", 101
    s.commit()
    backend._drop_loser_to_lb(s, f1)

    assert backend._resolve_lb_byes(s) >= 1
    lb0 = _fight_at(s, bid, "lb", 0, 0)
    assert lb0.status == "bye" and lb0.winner_id == 102
    assert (lb0.participant1_id, lb0.participant2_id) == (102, 102)
    # Sieger ist via lb_advance[0](0)=(1,0,1) in die Bronze vorgerückt.
    assert _fight_at(s, bid, "lb", 1, 0).participant1_id == 102
    # Idempotent.
    assert backend._resolve_lb_byes(s) == 0


def test_lb_bye_cascade_two_dead(session):
    """Zwei benachbarte WB-Freilose ⇒ lb r0 p0 voll tot (Kaskade); ein realer Kämpfer
    im Folge-Bronze-Slot wird dann seinerseits Walkover."""
    s = session
    bid = _seed_8er_custom(s, 51, [
        (0, 110, 110, 110, "bye"),
        (1, 111, 111, 111, "bye"),
        (2, 112, 113, None, "pending"),
        (3, 114, 115, None, "pending"),
    ])
    backend._ensure_ko_tree_materialized(s)
    backend._resolve_pending_byes(s)
    backend._resolve_lb_byes(s)

    lb00 = _fight_at(s, bid, "lb", 0, 0)
    assert lb00.status == "bye" and lb00.winner_id is None
    assert lb00.participant1_id is None and lb00.participant2_id is None

    # Realer SF-Verlierer trifft im Bronze (lb r1 p0) auf den toten (kaskadierten) Slot.
    bz0 = _fight_at(s, bid, "lb", 1, 0)
    bz0.participant2_id = 200
    s.commit()
    backend._resolve_lb_byes(s)
    bz0 = _fight_at(s, bid, "lb", 1, 0)
    assert bz0.status == "bye" and bz0.winner_id == 200


def test_finalize_with_walkover_and_dead_bronze(session):
    """Finalize: Walkover-Bronze (bye+winner) zählt als 3.; voll-toter Bronze ⇒
    third_place=None, blockiert den Abschluss nicht."""
    s = session
    bid = _seed_8er_custom(s, 52, [
        (pos, 2 * pos + 1, 2 * pos + 2, 2 * pos + 1, "finished") for pos in range(4)
    ], base=640)
    backend._ensure_ko_tree_materialized(s)
    fin = _fight_at(s, bid, "wb", 2, 0)
    fin.participant1_id, fin.participant2_id = 1, 3
    fin.status, fin.winner_id = "finished", 1
    bz0 = _fight_at(s, bid, "lb", 1, 0)
    bz0.participant1_id = bz0.participant2_id = 7
    bz0.status, bz0.winner_id = "bye", 7          # Walkover-Bronze
    bz1 = _fight_at(s, bid, "lb", 1, 1)
    bz1.status, bz1.winner_id = "bye", None        # voll tot
    s.commit()

    res = backend._finalize_doppel_ko_bracket(s, fin)
    assert res is not None
    b = s.get(BracketModel, bid)
    assert b.status == "completed"
    assert (b.first_place, b.second_place) == (1, 3)
    assert {b.third_place_1, b.third_place_2} == {7, None}

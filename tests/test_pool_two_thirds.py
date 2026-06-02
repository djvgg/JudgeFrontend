# SPDX-FileCopyrightText: 2026 TOP Team Combat Control
# SPDX-License-Identifier: GPL-3.0-or-later

"""Einzelpool-Platzierungen: zwei dritte Plätze ab >3 Teilnehmern.

Treibt `_finalize_pool_bracket_if_complete` direkt gegen eine In-Memory-SQLite-DB.

Decision 2026-06-01 (/wsp-architect): Einzelpool mit MEHR ALS DREI Teilnehmern
(`len(standings) > 3`) bekommt `third_place_2 = standings[3]` (Rang 3 + Rang 4
Bronze). Genau-3-Pool ⇒ nur ein Dritter. Siehe WSP/CLAUDE.md.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main as backend
from src.database import Base, BracketModel, FightModel


@pytest.fixture()
def session():
    # Isolierte In-Memory-DB — nie das echte Postgres aus der Env anfassen.
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _seed_pool(s, pairs, winners):
    """Pool-Bracket (bracket_type='pools') mit fertigen Round-Robin-Fights.

    pairs: Liste (p1, p2); winners[i]: gewinnende gp_id von pairs[i].
    Sieger bekommt Score 10, Verlierer 0 (Siege dominieren die Rangliste).
    Returns: (bracket_id, letzter Fight).
    """
    bid = 20
    s.add(BracketModel(id=bid, bracket_type="pools", status="pending"))
    last = None
    for pos, ((p1, p2), w) in enumerate(zip(pairs, winners)):
        score1, score2 = (10, 0) if w == p1 else (0, 10)
        last = FightModel(id=200 + pos, bracket_id=bid, bracket_phase="pool",
                          round=0, pos_in_round=pos, participant1_id=p1,
                          participant2_id=p2, score1=score1, score2=score2,
                          winner_id=w, status="finished", fight_number=pos + 1)
        s.add(last)
    s.commit()
    return bid, last


def test_4er_pool_zwei_dritte_plaetze(session):
    s = session
    # 4er-Round-Robin, transitiv: Siege 1>2>3>4.
    pairs = [(1, 2), (1, 3), (1, 4), (2, 3), (2, 4), (3, 4)]
    winners = [1, 1, 1, 2, 2, 3]
    bid, last = _seed_pool(s, pairs, winners)

    backend._finalize_pool_bracket_if_complete(s, last)

    b = s.get(BracketModel, bid)
    assert b.status == "completed"
    assert b.first_place == 1
    assert b.second_place == 2
    assert b.third_place_1 == 3
    assert b.third_place_2 == 4   # Rang 4 wird zweite Bronze


def test_3er_pool_nur_ein_dritter(session):
    s = session
    # 3er-Round-Robin, transitiv: Siege 1>2>3.
    pairs = [(1, 2), (1, 3), (2, 3)]
    winners = [1, 1, 2]
    bid, last = _seed_pool(s, pairs, winners)

    backend._finalize_pool_bracket_if_complete(s, last)

    b = s.get(BracketModel, bid)
    assert b.status == "completed"
    assert b.first_place == 1
    assert b.second_place == 2
    assert b.third_place_1 == 3
    assert b.third_place_2 is None   # genau 3 ⇒ kein zweiter Dritter


def test_event_dict_enthaelt_beide_dritten(session):
    s = session
    pairs = [(1, 2), (1, 3), (1, 4), (2, 3), (2, 4), (3, 4)]
    winners = [1, 1, 1, 2, 2, 3]
    _bid, last = _seed_pool(s, pairs, winners)

    evt = backend._finalize_pool_bracket_if_complete(s, last)

    assert evt["event"] == "BRACKET_COMPLETED"
    assert evt["placements"]["third_1"] == 3
    assert evt["placements"]["third_2"] == 4

# SPDX-FileCopyrightText: 2026 TOP Team Combat Control
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pool-Platzierungs-Tiebreaker (Decision 2026-06-08, CLAUDE.md).

Siege gesamt → direkter Vergleich (Siege nur gegen die anderen Gleichplatzierten)
→ stabile gp.id (Los). PUNKTE/Pluspunkt-Differenz zählen NICHT. Isoliertes
In-Memory-SQLite; testet `main._compute_pool_standings` direkt.
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


def _add(s, bid, pos, p1, p2, winner, s1, s2):
    s.add(FightModel(id=300 + pos, bracket_id=bid, bracket_phase="pool", round=0,
                     pos_in_round=pos, participant1_id=p1, participant2_id=p2,
                     score1=s1, score2=s2, winner_id=winner, status="finished",
                     fight_number=pos + 1))


def test_h2h_beats_points(session):
    """4er-Pool: 1 und 2 je 2 Siege. 2 schlägt 1 direkt, 1 hat aber riesige
    Pluspunkte. Erwartung: 2 vor 1 (direkter Vergleich), Punkte irrelevant."""
    s = session
    bid = 20
    s.add(BracketModel(id=bid, bracket_type="pools", status="pending"))
    # 1 schlägt 3,4 mit 100:0 (riesige Pluspunkte); verliert gegen 2 mit 0:1
    _add(s, bid, 0, 1, 3, 1, 100, 0)
    _add(s, bid, 1, 1, 4, 1, 100, 0)
    _add(s, bid, 2, 2, 1, 2, 1, 0)      # 2 schlägt 1 direkt (knapp)
    _add(s, bid, 3, 2, 3, 2, 1, 0)
    _add(s, bid, 4, 3, 4, 3, 1, 0)      # 3 schlägt 4
    _add(s, bid, 5, 4, 2, 4, 1, 0)      # 4 schlägt 2
    s.commit()
    # Siege: 1=2 (3,4), 2=2 (1,3), 3=1 (4), 4=1 (2)
    order = backend._compute_pool_standings(s, bid)
    assert order[0] == 2 and order[1] == 1, order   # H2H: 2 vor 1 trotz 1s 200 Pluspunkten
    assert set(order[2:]) == {3, 4}


def test_three_way_circle_falls_to_id(session):
    """3er-Ringschluss (1→2, 2→3, 3→1), alle 1 Sieg → stabile gp.id."""
    s = session
    bid = 21
    s.add(BracketModel(id=bid, bracket_type="pools", status="pending"))
    _add(s, bid, 0, 1, 2, 1, 10, 0)   # 1 schlägt 2
    _add(s, bid, 1, 2, 3, 2, 10, 0)   # 2 schlägt 3
    _add(s, bid, 2, 1, 3, 3, 0, 10)   # 3 schlägt 1
    s.commit()
    order = backend._compute_pool_standings(s, bid)
    assert order == [1, 2, 3], order   # alle gleich → deterministische id-Reihenfolge


def test_transitive_pool_unaffected(session):
    """Transitiver 3er (1>2>3): klare Reihenfolge, kein Tie."""
    s = session
    bid = 22
    s.add(BracketModel(id=bid, bracket_type="pools", status="pending"))
    _add(s, bid, 0, 1, 2, 1, 10, 0)
    _add(s, bid, 1, 1, 3, 1, 10, 0)
    _add(s, bid, 2, 2, 3, 2, 10, 0)
    s.commit()
    assert backend._compute_pool_standings(s, bid) == [1, 2, 3]

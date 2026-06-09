# SPDX-FileCopyrightText: 2026 TOP Team Combat Control
# SPDX-License-Identifier: GPL-3.0-or-later

"""Solo-Pool = Auto-Platz-1 (Decision 2026-06-09, Merlin).

edv erzeugt fuer einen U9/U11-Gewichtsausreisser einen 1-Teilnehmer-Pool
(bracket_type='pools', 0 Fights). Ein 0-Fight-Pool triggert den normalen
Pool-Finalize-Hook nie, also schliesst `_finalize_solo_pool_brackets` ihn im
Lade-Pfad ab: der eine GroupParticipant wird Platz 1, status='completed'.

Treibt die Funktion direkt gegen eine In-Memory-SQLite-DB.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main as backend
from src.database import (
    Base,
    BracketModel,
    FightModel,
    GroupParticipantModel,
)


@pytest.fixture()
def session():
    # Isolierte In-Memory-DB — nie das echte Postgres aus der Env anfassen.
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _add_group_participant(s, gp_id, group_id):
    s.add(GroupParticipantModel(id=gp_id, group_id=group_id, participant_id=gp_id))


def test_solo_pool_gets_first_place_and_completed(session):
    s = session
    # 1-Teilnehmer-Pool: Bracket auf Gruppe 50, genau ein GroupParticipant, 0 Fights.
    s.add(BracketModel(id=30, group_id=50, bracket_type="pools", status="pending"))
    _add_group_participant(s, gp_id=900, group_id=50)
    s.commit()

    finalized = backend._finalize_solo_pool_brackets(s)

    assert finalized == [30]
    b = s.get(BracketModel, 30)
    assert b.status == "completed"
    assert b.first_place == 900
    assert b.second_place is None
    assert b.third_place_1 is None
    assert b.third_place_2 is None
    # keine Fights angelegt
    assert s.query(FightModel).filter(FightModel.bracket_id == 30).count() == 0


def test_idempotent_second_call_is_noop(session):
    s = session
    s.add(BracketModel(id=30, group_id=50, bracket_type="pools", status="pending"))
    _add_group_participant(s, gp_id=900, group_id=50)
    s.commit()

    assert backend._finalize_solo_pool_brackets(s) == [30]
    # zweiter Lauf: bereits completed ⇒ nichts mehr zu tun
    assert backend._finalize_solo_pool_brackets(s) == []
    assert s.get(BracketModel, 30).first_place == 900


def test_two_participant_pool_untouched(session):
    s = session
    # 2er-Pool (best-of-three, 3 Fights) darf NICHT solo-finalisiert werden —
    # er hat Fights und laeuft ueber den regulaeren Finalize-Pfad.
    s.add(BracketModel(id=31, group_id=51, bracket_type="pools", status="pending"))
    _add_group_participant(s, gp_id=901, group_id=51)
    _add_group_participant(s, gp_id=902, group_id=51)
    for pos in range(3):
        s.add(FightModel(id=300 + pos, bracket_id=31, bracket_phase="pool",
                         round=0, pos_in_round=pos, participant1_id=901,
                         participant2_id=902, status="pending", fight_number=pos + 1))
    s.commit()

    assert backend._finalize_solo_pool_brackets(s) == []
    b = s.get(BracketModel, 31)
    assert b.status == "pending"
    assert b.first_place is None


def test_empty_group_zero_fights_untouched(session):
    s = session
    # Degenerierter Schutz: 0 Teilnehmer + 0 Fights ⇒ kein Solo (len(gps) != 1).
    s.add(BracketModel(id=32, group_id=52, bracket_type="pools", status="pending"))
    s.commit()

    assert backend._finalize_solo_pool_brackets(s) == []
    assert s.get(BracketModel, 32).status == "pending"


def test_multi_participant_pool_not_yet_materialized_untouched(session):
    s = session
    # 3 GroupParticipants, aber Fights noch nicht angelegt (transienter Zustand).
    # Darf NICHT solo-finalisiert werden — len(gps) != 1 schuetzt davor.
    s.add(BracketModel(id=33, group_id=53, bracket_type="pools", status="pending"))
    for gp in (910, 911, 912):
        _add_group_participant(s, gp_id=gp, group_id=53)
    s.commit()

    assert backend._finalize_solo_pool_brackets(s) == []
    assert s.get(BracketModel, 33).first_place is None


def test_non_pool_bracket_untouched(session):
    s = session
    # KO-Bracket mit 1 GP (theoretisch) wird nicht angefasst — nur 'pools'.
    s.add(BracketModel(id=34, group_id=54, bracket_type="ko", status="pending"))
    _add_group_participant(s, gp_id=920, group_id=54)
    s.commit()

    assert backend._finalize_solo_pool_brackets(s) == []
    assert s.get(BracketModel, 34).first_place is None

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.bracket_manager import BracketManager
from src.database import (
    BracketModel,
    FightModel,
    GroupModel,
    GroupParticipantModel,
    ParticipantModel,
)

load_dotenv()
engine = create_engine(os.getenv("DATABASE_URL"))
SessionLocal = sessionmaker(bind=engine)


def test_pool():
    session = SessionLocal()
    try:
        # Create a fresh test pool
        g = GroupModel(gender="m", age_group="TEST", weight_class="POOL")
        session.add(g)
        session.flush()

        b = BracketModel(group_id=g.id, bracket_type="POOL", status="ongoing")
        session.add(b)
        session.flush()

        # Create 3 participants
        participants = []
        for name in ["Alpha", "Bravo", "Charlie"]:
            p = ParticipantModel(first_name=name, last_name="Test", club="Club")
            session.add(p)
            session.flush()
            gp = GroupParticipantModel(group_id=g.id, participant_id=p.id)
            session.add(gp)
            session.flush()
            participants.append(gp)

        p1, p2, p3 = participants

        # Fights Setup
        # Alpha beats Bravo (10 - 0)
        # Bravo beats Charlie (10 - 0)
        # Charlie beats Alpha (10 - 0)
        # Everyone has 1 Win and 10 Points.
        # This tests the point difference fallback (which will also tie here 10-10=0 each).

        f1 = FightModel(
            bracket_id=b.id,
            participant1_id=p1.id,
            participant2_id=p2.id,
            status="completed",
            winner_id=p1.id,
            score1=10,
            score2=0,
            bracket_phase="pool",
        )
        f2 = FightModel(
            bracket_id=b.id,
            participant1_id=p2.id,
            participant2_id=p3.id,
            status="completed",
            winner_id=p2.id,
            score1=10,
            score2=0,
            bracket_phase="pool",
        )
        f3 = FightModel(
            bracket_id=b.id,
            participant1_id=p3.id,
            participant2_id=p1.id,
            status="completed",
            winner_id=p3.id,
            score1=10,
            score2=0,
            bracket_phase="pool",
        )
        session.add_all([f1, f2, f3])
        session.flush()

        # Let's test a Head-to-Head specific tie in an isolated state
        participants2 = []
        p4 = ParticipantModel(first_name="Delta", last_name="Test", club="Club")
        p5 = ParticipantModel(first_name="Echo", last_name="Test", club="Club")
        p6 = ParticipantModel(first_name="Foxtrot", last_name="Test", club="Club")
        p7 = ParticipantModel(first_name="Golf", last_name="Test", club="Club")
        session.add_all([p4, p5, p6, p7])
        session.flush()

        gp4 = GroupParticipantModel(group_id=g.id, participant_id=p4.id)
        gp5 = GroupParticipantModel(group_id=g.id, participant_id=p5.id)
        gp6 = GroupParticipantModel(group_id=g.id, participant_id=p6.id)
        gp7 = GroupParticipantModel(group_id=g.id, participant_id=p7.id)
        session.add_all([gp4, gp5, gp6, gp7])
        session.flush()
        participants2.extend([gp4, gp5, gp6, gp7])

        # Scenario: Delta and Echo both finish with exactly 2 Wins, 20 Points.
        # But Delta beat Echo directly.
        f4 = FightModel(
            bracket_id=b.id,
            participant1_id=gp4.id,
            participant2_id=gp5.id,
            status="completed",
            winner_id=gp4.id,
            score1=10,
            score2=0,
            bracket_phase="pool",
        )  # D beats E
        f5 = FightModel(
            bracket_id=b.id,
            participant1_id=gp4.id,
            participant2_id=gp6.id,
            status="completed",
            winner_id=gp4.id,
            score1=10,
            score2=0,
            bracket_phase="pool",
        )  # D beats F
        f6 = FightModel(
            bracket_id=b.id,
            participant1_id=gp4.id,
            participant2_id=gp7.id,
            status="completed",
            winner_id=gp7.id,
            score1=0,
            score2=10,
            bracket_phase="pool",
        )  # D loses to G

        f7 = FightModel(
            bracket_id=b.id,
            participant1_id=gp5.id,
            participant2_id=gp6.id,
            status="completed",
            winner_id=gp5.id,
            score1=10,
            score2=0,
            bracket_phase="pool",
        )  # E beats F
        f8 = FightModel(
            bracket_id=b.id,
            participant1_id=gp5.id,
            participant2_id=gp7.id,
            status="completed",
            winner_id=gp5.id,
            score1=10,
            score2=0,
            bracket_phase="pool",
        )  # E beats G

        # D = 2W, 20Pts (wins against E, F). PtsAgst = 10 (loses to G) => Diff = +10
        # E = 2W, 20Pts (wins against F, G). PtsAgst = 10 (loses to D) => Diff = +10
        # Perfect tie!

        session.add_all([f4, f5, f6, f7, f8])
        session.flush()

        # Run Calculation just for D, E, F, G
        p_data2 = [
            {
                "id": gp.id,
                "name": f"{session.query(ParticipantModel).get(gp.participant_id).first_name}",
                "club": "",
            }
            for gp in participants2
        ]
        all_fights2 = (
            session.query(FightModel)
            .filter_by(bracket_id=b.id)
            .filter(FightModel.participant1_id.in_([gp4.id, gp5.id, gp6.id, gp7.id]))
            .all()
        )

        standings2 = BracketManager.calculate_pool_standings(all_fights2, p_data2)

        print("--- STANDINGS RESULTS ---")
        for idx, s in enumerate(standings2):
            diff = s["points"] - s["points_against"]
            print(
                f"{idx + 1}. {s['name']} - Wins: {s['wins']}, Pts: {s['points']}, PtsAgst: {s['points_against']}, Diff: {diff}"
            )

        # Assertions
        assert standings2[0]["name"] == "Delta", "Delta should win H2H over Echo"
        assert standings2[1]["name"] == "Echo", "Echo is second"
        print("Success! Head-to-Head logic properly resolved Delta over Echo.")

        session.rollback()  # Don't save test data
    except Exception as e:
        print(f"Test Failed: {e}")
        session.rollback()
    finally:
        session.close()


if __name__ == "__main__":
    test_pool()

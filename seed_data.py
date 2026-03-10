from sqlalchemy import text

from src.database import engine


def seed():
    with engine.begin() as conn:
        # Create participants
        conn.execute(text("""
            INSERT INTO participants (id, first_name, last_name)
            VALUES
                (101, 'Jean', 'Dupont'),
                (102, 'Marc', 'Martin'),
                (103, 'Lucien', 'Bieri'),
                (104, 'Paul', 'Girod')
            ON CONFLICT (id) DO NOTHING
        """))

        # Create bracket
        conn.execute(text("""
            INSERT INTO brackets (id, group_id, mat_id, bracket_type, status)
            VALUES (99, 1, 1, 'DOUBLE_ELIMINATION', 'ongoing')
            ON CONFLICT (id) DO NOTHING
        """))

        # Create fights
        conn.execute(text("""
            INSERT INTO fights (id, bracket_id, participant1_id, participant2_id, status, bracket_phase, round, pos_in_round)
            VALUES
                (1001, 99, 101, 102, 'finished', 'wb', 1, 1),
                (1002, 99, 103, 104, 'ongoing', 'wb', 1, 2),
                (1003, 99, 101, NULL, 'upcoming', 'wb', 2, 1),
                (1004, 99, 102, NULL, 'upcoming', 'lb', 1, 1)
            ON CONFLICT (id) DO NOTHING
        """))

        # Set scores for the finished fight
        conn.execute(text("""
            UPDATE fights
            SET winner_id=101, score1=10, score2=0
            WHERE id=1001
        """))

    print('Successfully seeded dummy Double Elimination data (Bracket 99).')

if __name__ == "__main__":
    seed()

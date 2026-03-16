import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
engine = create_engine(os.getenv("DATABASE_URL"))


def diagnose():
    with engine.connect() as conn:
        print("--- Table Counts ---")
        for table in ["participants", "groups", "brackets", "fights", "group_participants"]:
            try:
                res = conn.execute(text(f"SELECT count(*) FROM {table}"))
                count = res.scalar()
                print(f"{table}: {count}")
            except Exception as e:
                print(f"{table}: Error - {e}")

        print("\n--- Brackets in Database ---")
        res = conn.execute(
            text("""
            SELECT b.id, b.bracket_type, g.age_group, g.weight_class,
                   (SELECT count(*) FROM fights WHERE bracket_id = b.id) as fight_count
            FROM brackets b
            JOIN groups g ON b.group_id = g.id
            ORDER BY b.id DESC LIMIT 10
        """)
        )
        rows = res.fetchall()
        for r in rows:
            print(f"ID: {r[0]} | Type: {r[1]} | Category: {r[2]} {r[3]} | Fights: {r[4]}")

        print("\n--- Recent Fights (Upcoming) ---")
        res = conn.execute(
            text("""
            SELECT f.id, f.bracket_id, f.bracket_phase, f.round, f.pos_in_round, f.status, f.participant1_id, f.participant2_id
            FROM fights f
            WHERE f.status = 'upcoming'
            ORDER BY f.id DESC LIMIT 5
        """)
        )
        for f in res:
            print(
                f"Fight ID: {f[0]} | Bracket: {f[1]} | Phase: {f[2]} | Round: {f[3]} | Pos: {f[4]} | P1: {f[6]} | P2: {f[7]}"
            )


if __name__ == "__main__":
    diagnose()

from sqlalchemy import text
from src.database import engine


def check():
    with engine.connect() as conn:
        print("Checking Brackets...")
        brackets = conn.execute(text("SELECT id, bracket_type FROM brackets")).fetchall()
        if not brackets:
            print("No brackets found in database.")
            return

        for b_id, b_type in brackets:
            fights_count = conn.execute(
                text(f"SELECT COUNT(*) FROM fights WHERE bracket_id = {b_id}")
            ).scalar()
            print(f"Bracket ID: {b_id}, Type: {b_type}, Fights: {fights_count}")


if __name__ == "__main__":
    check()

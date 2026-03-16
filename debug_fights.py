from sqlalchemy import text
from src.database import engine


def check():
    with engine.connect() as conn:
        print("Checking Fights...")
        fights = conn.execute(
            text(
                "SELECT id, bracket_id, status, table_id, bracket_phase FROM fights ORDER BY id DESC LIMIT 20"
            )
        ).fetchall()
        for f in fights:
            print(f)


if __name__ == "__main__":
    check()

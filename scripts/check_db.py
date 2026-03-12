from sqlalchemy import text

from backend.database import engine


def check():
    with engine.connect() as conn:
        groups = conn.execute(text("SELECT id FROM groups LIMIT 1")).fetchone()
        mats = conn.execute(text("SELECT id FROM mats LIMIT 1")).fetchone()
        print(f"GroupID: {groups[0] if groups else 'None'}")
        print(f"MatID: {mats[0] if mats else 'None'}")

if __name__ == "__main__":
    check()

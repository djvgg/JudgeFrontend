from dotenv import load_dotenv
from sqlalchemy import text

from src.database import FightModel, SessionLocal, engine


def clear_future_participants():
    load_dotenv(override=True)

    # Drop NOT NULL constraints if they exist
    with engine.connect() as conn, conn.begin():
        try:
            conn.execute(text("ALTER TABLE fights ALTER COLUMN participant1_id DROP NOT NULL;"))
            conn.execute(text("ALTER TABLE fights ALTER COLUMN participant2_id DROP NOT NULL;"))
            print("Successfully altered columns to allow NULLs.")
        except Exception as e:
            print(f"Could not alter columns (maybe already nullable): {e}")

    # Set future rounds to None
    with SessionLocal() as session:
        fights = session.query(FightModel).filter(FightModel.round > 0).all()
        for f in fights:
            f.participant1_id = None
            f.participant2_id = None
        session.commit()
        print(f"Cleared participant IDs for {len(fights)} future fights (round > 0).")

if __name__ == "__main__":
    clear_future_participants()

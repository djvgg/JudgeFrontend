import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.database import BracketModel, FightModel

load_dotenv()
engine = create_engine(os.getenv("DATABASE_URL"))
SessionLocal = sessionmaker(bind=engine)

def assign_tables():
    session = SessionLocal()
    try:
        # Find POOL brackets
        pool_brackets = session.query(BracketModel).filter_by(bracket_type="POOL").all()
        b_ids = [b.id for b in pool_brackets]

        if not b_ids:
            print("No POOL brackets found!")
            return

        # Update all fights in POOL brackets to table_id="1"
        fights = session.query(FightModel).filter(FightModel.bracket_id.in_(b_ids)).all()
        for i, f in enumerate(fights):
            if not f.table_id:
                f.table_id = "1"
            if not f.fight_number:
                f.fight_number = 100 + i # Give it a high number so it shows up

        session.commit()
        print(f"Updated {len(fights)} POOL matches to Table 1.")

    except Exception as e:
        session.rollback()
        print(f"Error: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    assign_tables()

import os
from dotenv import load_dotenv
from src.database import SessionLocal, FightModel

def assign_tables_by_bracket():
    load_dotenv(override=True)
    with SessionLocal() as session:
        fights = session.query(FightModel).all()
        for f in fights:
            if f.bracket_id:
                # Group all fights for a given bracket onto one table by default
                f.table_id = str((f.bracket_id % 4) + 1)
        session.commit()
        print(f"Assigned solid table_ids grouped by bracket for {len(fights)} fights.")

if __name__ == "__main__":
    assign_tables_by_bracket()

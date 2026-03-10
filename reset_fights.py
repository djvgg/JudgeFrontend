import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from src.database import SessionLocal, FightModel

def reset_fights():
    load_dotenv(override=True)
    with SessionLocal() as session:
        fights = session.query(FightModel).all()
        for f in fights:
            f.status = 'pending'
            f.score1 = 0
            f.score2 = 0
            f.winner_id = None
        session.commit()
        print(f"Resetted {len(fights)} fights to 'pending' state.")

if __name__ == "__main__":
    reset_fights()

import os
from sqlalchemy import create_engine, Column, Integer, String, JSON
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

# Load environment variables, overriding any cached ones during uvicorn reloads
load_dotenv(override=True)

DATABASE_URL = os.getenv("DATABASE_URL")

# Create sync engine
engine = create_engine(DATABASE_URL)

# Create session factory (sync)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class MatchModel(Base):
    """
    Legacy SQLAlchemy model representing our initial JSON structure.
    Keep this around if we need to rollback to the old table 'matches'.
    """
    __tablename__ = "matches"
    
    matchId = Column(Integer, primary_key=True, index=True)
    tableId = Column(String)
    fightNr = Column(Integer)
    category = Column(String)
    bracketFile = Column(String)
    round = Column(Integer)
    posInRound = Column(Integer)
    p1 = Column(JSON)
    p2 = Column(JSON)
    status = Column(String)
    order = Column(Integer)
    restTimeMin = Column(Integer)
    nextMatchId = Column(Integer, nullable=True)
    nextMatchPos = Column(String, nullable=True)

class ParticipantModel(Base):
    """
    Native 'participants' table from the backend.
    """
    __tablename__ = "participants"
    __table_args__ = {'extend_existing': True}
    
    id = Column(Integer, primary_key=True)
    first_name = Column(String)
    last_name = Column(String)
    gender = Column(String)
    club = Column(String)

class FightModel(Base):
    """
    Native 'fights' table from the backend.
    """
    __tablename__ = "fights"
    __table_args__ = {'extend_existing': True}
    
    id = Column(Integer, primary_key=True)
    bracket_id = Column(Integer)
    participant1_id = Column(Integer)
    participant2_id = Column(Integer)
    fight_number = Column(Integer)
    score1 = Column(Integer, nullable=True)
    score2 = Column(Integer, nullable=True)
    duration = Column(Integer, nullable=True)
    status = Column(String)
    bracket_phase = Column(String)
    round = Column(Integer, nullable=True)
    pos_in_round = Column(Integer, nullable=True)
    pool_index = Column(Integer, nullable=True)
    winner_id = Column(Integer, nullable=True)

def get_db():
    """ Dependency for getting a database session """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """ Initialize the database tables if they don't exist """
    Base.metadata.create_all(bind=engine)

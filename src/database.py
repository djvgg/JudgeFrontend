import os

from dotenv import load_dotenv
from sqlalchemy import JSON, Column, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

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

    match_id = Column(Integer, primary_key=True, index=True)
    table_id = Column(String)
    fight_nr = Column(Integer)
    category = Column(String)
    bracket_file = Column(String)
    round = Column(Integer)
    pos_in_round = Column(Integer)
    p1 = Column(JSON)
    p2 = Column(JSON)
    status = Column(String)
    order = Column(Integer)
    rest_time_min = Column(Integer)
    next_match_id = Column(Integer, nullable=True)
    next_match_pos = Column(String, nullable=True)

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

class GroupParticipantModel(Base):
    """
    Join table 'group_participants'. fights.participant{1,2}_id and
    fights.winner_id reference this table's id, not participants.id.
    """
    __tablename__ = "group_participants"
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True)
    group_id = Column(Integer)
    participant_id = Column(Integer)

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
    table_id = Column(String, nullable=True)
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

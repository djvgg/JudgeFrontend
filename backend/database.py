import os
import logging
from dotenv import load_dotenv
from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.exc import OperationalError

# Setup local logging for database initialization
logging.basicConfig(level=logging.INFO)
db_logger = logging.getLogger("Database")

# Load environment variables
load_dotenv(override=True)

DATABASE_URL = os.getenv("DATABASE_URL")
SQLITE_URL = "sqlite:///./local_tournament.db"

def get_engine():
    """
    Creates the database engine with an automatic fallback to SQLite 
    if the primary PostgreSQL connection fails.
    """
    primary_url = os.getenv("DATABASE_URL")
    
    try:
        # 1. Try PostgreSQL (Primary)
        if primary_url and primary_url.startswith("postgresql"):
            db_logger.info(f"Attempting to connect to PostgreSQL...")
            # We use a short search_timeout to avoid 30s hangs at startup
            temp_engine = create_engine(
                primary_url, 
                pool_pre_ping=True, 
                pool_recycle=600,
                connect_args={"connect_timeout": 3}  # 3 seconds timeout for fast fail
            )
            with temp_engine.connect() as conn:
                db_logger.info("PostgreSQL connection successful.")
                return temp_engine
    except Exception as e:
        db_logger.warning(f"PostgreSQL Connection Failed: {e}")

    # 2. Fallback to SQLite (Offline Mode)
    db_logger.warning("Switching to OFFLINE MODE (Local SQLite Database).")
    return create_engine(
        SQLITE_URL, 
        connect_args={"check_same_thread": False}
    )

engine = get_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class GroupModel(Base):
    __tablename__ = "groups"
    __table_args__ = {"extend_existing": True}
    id = Column(Integer, primary_key=True)
    gender = Column(String)
    age_group = Column(String)
    weight_class = Column(String)

class BracketModel(Base):
    __tablename__ = "brackets"
    __table_args__ = {"extend_existing": True}
    id = Column(Integer, primary_key=True)
    group_id = Column(Integer)
    mat_id = Column(Integer)
    bracket_type = Column(String)
    status = Column(String)
    first_place = Column(Integer, nullable=True)
    second_place = Column(Integer, nullable=True)
    third_place_1 = Column(Integer, nullable=True)
    third_place_2 = Column(Integer, nullable=True)

class ParticipantModel(Base):
    __tablename__ = "participants"
    __table_args__ = {"extend_existing": True}
    id = Column(Integer, primary_key=True)
    first_name = Column(String)
    last_name = Column(String)
    gender = Column(String)
    club = Column(String)

class GroupParticipantModel(Base):
    __tablename__ = "group_participants"
    __table_args__ = {"extend_existing": True}
    id = Column(Integer, primary_key=True)
    group_id = Column(Integer)
    participant_id = Column(Integer)

class FightModel(Base):
    __tablename__ = "fights"
    __table_args__ = {"extend_existing": True}
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
    table_id = Column(String, nullable=True)

def init_db():
    Base.metadata.create_all(bind=engine)

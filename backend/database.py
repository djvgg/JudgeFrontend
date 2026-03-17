import os

from dotenv import load_dotenv
from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Load environment variables, overriding any cached ones during uvicorn reloads
load_dotenv(override=True)

DATABASE_URL = os.getenv("DATABASE_URL")

# Create sync engine.
# pool_pre_ping: test each pooled connection before use — transparently
#   reconnects if PostgreSQL closed the connection while idle (e.g. after a
#   network blip or the DB server restarted).
# pool_recycle: recycle connections after 10 min so they're never older than
#   PostgreSQL's idle-session timeout (default is often 10–30 min in hosted DBs).
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=600)

# Create session factory (sync)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


class GroupModel(Base):
    """
    Groups participants by gender, age group, and weight class.
    """

    __tablename__ = "groups"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)
    gender = Column(String)
    age_group = Column(String)
    weight_class = Column(String)


class BracketModel(Base):
    """
    Groups fights into a category and defines the tournament system.
    """

    __tablename__ = "brackets"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)
    group_id = Column(Integer)
    mat_id = Column(Integer)
    bracket_type = Column(String)  # e.g. 'POOL', 'DOUBLE_ELIMINATION', 'SINGLE_ELIMINATION'
    status = Column(String)
    first_place = Column(Integer, nullable=True)
    second_place = Column(Integer, nullable=True)
    third_place_1 = Column(Integer, nullable=True)
    third_place_2 = Column(Integer, nullable=True)


class ParticipantModel(Base):
    """
    Native 'participants' table from the backend.
    """

    __tablename__ = "participants"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)
    first_name = Column(String)
    last_name = Column(String)
    gender = Column(String)
    club = Column(String)


class GroupParticipantModel(Base):
    """
    Intersection table mapping participants to groups.
    """

    __tablename__ = "group_participants"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True)
    group_id = Column(Integer)
    participant_id = Column(Integer)


class FightModel(Base):
    """
    Native 'fights' table from the backend.
    """

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
    """Ensure all DB tables exist. Alembic migrations are run via `make migrate`."""
    Base.metadata.create_all(bind=engine)

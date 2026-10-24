"""
Database engine and session management for QualityGuard AI.
Uses SQLite for zero-external-dependency local operation.
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# DB file lives alongside the backend package so `uvicorn main:app` from
# backend/ finds it regardless of the caller's current working directory.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "qualityguard.db")
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

# check_same_thread=False is required for SQLite when accessed from
# FastAPI's threaded request handlers.
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency that yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables. Safe to call repeatedly (no-op if tables exist)."""
    # Import all model modules here so their classes are registered on
    # Base.metadata before create_all runs.
    from backend.models import (  # noqa: F401
        user, machine, sensor, inspection, production, maintenance,
        mes, defect, root_cause, recommendation, alert, audit,
        chat, report, knowledge_base, preprod_risk, event_trace,
        batch_quality, learning_trend,
    )
    Base.metadata.create_all(bind=engine)

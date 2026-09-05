"""
Database connection setup. Defaults to a local SQLite file so the app
works out of the box with zero setup; point DATABASE_URL at Postgres/MySQL
for production use.
"""
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config.settings import settings

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def init_db():
    """Create all tables. Safe to call repeatedly - no-op if they exist."""
    from app.database import models  # noqa: F401 - ensures models are registered
    Base.metadata.create_all(bind=engine)


@contextmanager
def get_session():
    """Usage: with get_session() as session: ..."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

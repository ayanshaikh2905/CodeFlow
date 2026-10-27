"""SQLAlchemy engine and session dependency."""

from collections.abc import Generator
import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

DATABASE_URL = os.environ.get("CODEFLOW_DEMO_DATABASE_URL", "sqlite:///./codeflow_demo.db")


def make_engine(url: str = DATABASE_URL) -> Engine:
    """Create an engine, enabling SQLite's cross-thread test client use."""
    options = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=options)


engine = make_engine()
SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    """Yield a request-scoped database session."""
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()

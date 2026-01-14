"""Database connection utilities."""

import os
from typing import Optional
from sqlalchemy import create_engine, Engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool

# Database URL from environment variable
DATABASE_URL: Optional[str] = os.getenv("DATABASE_URL")

# Global engine instance (lazy initialization)
_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None


def get_engine() -> Engine:
    """Get or create the database engine."""
    global _engine
    if _engine is None:
        if DATABASE_URL is None:
            raise ValueError("DATABASE_URL environment variable is not set")
        _engine = create_engine(
            DATABASE_URL,
            poolclass=NullPool,  # Use NullPool for simplicity in single-process scenarios
            echo=False,  # Set to True for SQL query logging
        )
    return _engine


def get_session() -> Session:
    """Get a database session."""
    global _SessionLocal
    if _SessionLocal is None:
        engine = get_engine()
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return _SessionLocal()


def close_engine():
    """Close the database engine (for cleanup)."""
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings


def _ensure_master_database_exists() -> None:
    """Ensure the master database exists, auto-create if not."""
    import urllib.parse
    from sqlalchemy.exc import OperationalError
    
    try:
        # Try to connect to see if database exists
        test_engine = create_engine(settings.database_url, pool_pre_ping=True)
        with test_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        test_engine.dispose()
        return  # Database exists, nothing to do
    except OperationalError:
        # Database doesn't exist, create it using admin connection
        pass
    
    # Parse database name from URL
    parsed = urllib.parse.urlparse(settings.database_url)
    db_name = parsed.path.lstrip("/")
    
    if not db_name:
        raise ValueError("Cannot determine database name from DATABASE_URL")
    
    # Create database using admin connection
    admin_engine = create_engine(
        settings.db_admin_url,
        pool_pre_ping=True,
        isolation_level="AUTOCOMMIT",
    )
    
    with admin_engine.connect() as conn:
        # Check if database exists
        result = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :db_name"),
            {"db_name": db_name},
        )
        if not result.scalar():
            # Create database
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
            print(f"[AUTO-CREATE] Master database '{db_name}' created successfully")
    
    admin_engine.dispose()


# Ensure database exists before creating the engine
_ensure_master_database_exists()

master_engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
)

MasterSessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=master_engine
)


@contextmanager
def get_master_session() -> Generator[Session, None, None]:
    db = MasterSessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_master_db() -> Session:
    return MasterSessionLocal()

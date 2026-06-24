from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings

_engine = None
_SessionLocal = None


def _init_engine():
    global _engine, _SessionLocal
    if _engine is not None:
        return
    _engine = create_engine(settings.database_url, pool_pre_ping=True)
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


def get_session_local():
    _init_engine()
    return _SessionLocal


def get_db():
    db = get_session_local()()
    try:
        yield db
    finally:
        db.close()

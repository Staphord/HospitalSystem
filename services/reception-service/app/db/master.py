from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

_engine_kwargs = {"pool_pre_ping": True}
if "sqlite" not in settings.database_url:
    _engine_kwargs.update(pool_size=10, max_overflow=20, pool_recycle=3600)

master_engine = create_engine(settings.database_url, **_engine_kwargs)

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

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

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


def get_master_session():
    db = MasterSessionLocal()
    try:
        yield db
    finally:
        db.close()

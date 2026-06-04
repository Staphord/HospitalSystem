from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db


@contextmanager
def _db_session() -> Iterator[Session]:
    gen = get_db()
    db = next(gen)
    try:
        yield db
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def resolve_tenant_db_url(tenant_id: str) -> Optional[str]:
    try:
        with _db_session() as db:
            result = db.execute(
                text(
                    "SELECT db_connection_string FROM tenants "
                    "WHERE tenant_id = :tid AND is_active = true"
                ),
                {"tid": tenant_id},
            ).scalar()
            return str(result) if result else None
    except Exception:
        return None

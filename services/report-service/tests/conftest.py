"""Pytest conftest setup for report-service.

Settings are populated from the environment before anything imports the app, so
`pytest -q` works in this directory without a stack running. The values are
inert placeholders: no database is reached, no Keycloak is reached, and no
outbound call is made anywhere in this suite. Anything already exported by the
caller wins, so a real environment still overrides these.
"""

# Make `shared/` importable when the suite runs from the service directory.
#
# At runtime the deploy script symlinks the repository's `shared/` into each
# service that imports it, and docker-compose mounts it at the same place.
# Neither exists when pytest is run from inside the service, so the repository
# root goes on the path here - the same directory, reached the way a developer
# reaches it.
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


_TEST_ENV = {
    "ENVIRONMENT": "test",
    "DATABASE_URL": "postgresql://user:pass@localhost:5432/hospital_master",
    "SECRET_KEY": "test-secret-key",
    "REDIS_URL": "redis://localhost:6379/0",
    "KEYCLOAK_URL": "http://localhost:8080",
    "KEYCLOAK_REALM": "hospital",
    "KEYCLOAK_CLIENT_ID": "hospital-backend",
    "KEYCLOAK_CLIENT_SECRET": "test-client-secret",
    "KEYCLOAK_ADMIN_USERNAME": "admin",
    "KEYCLOAK_ADMIN_PASSWORD": "admin",
    "KEYCLOAK_INTROSPECT": "false",
    "ALLOWED_ORIGINS": "http://localhost:3000",
    "DEFAULT_HOSPITAL_ID": "default-hospital",
    # A real Fernet key, because the tenant DSN round-trip tests encrypt with
    # it. It protects nothing: it is a literal in a public test file.
    "TENANT_DB_ENCRYPTION_KEY": "cmVwb3J0LXNlcnZpY2UtdGVzdC1rZXktMzJieXRlcyE=",
}

for _key, _value in _TEST_ENV.items():
    os.environ.setdefault(_key, _value)

import sys  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core import config  # noqa: E402

sys.modules["app.config"] = config

from app.db.base import Base  # noqa: E402
from app.models.assistant import (  # noqa: E402,F401
    AssistantConversation,
    AssistantMessage,
)


@pytest.fixture
async def tenant_db():
    """An empty tenant database for the assistant history tables.

    SQLite in memory, built from the same declarative metadata the PostgreSQL
    migration creates, so a model change that the migration does not carry shows
    up here as a failure rather than in production. Each test gets its own
    engine, so nothing leaks between them.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()

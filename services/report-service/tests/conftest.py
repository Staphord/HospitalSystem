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


@pytest.fixture(autouse=True)
def _clear_assistant_config_cache():
    """Start every test with no cached assistant configuration.

    get_config() caches for ten seconds so a busy chat does not read the
    configuration row on every message. In a suite that is a leak: one test
    setting a credential would decide what the next few tests see. Clearing it
    on both sides of a test keeps them independent.

    No database is reachable here, so the resolved configuration is always the
    built-in defaults plus whatever a test has set on `settings` - which is
    what lets a test simulate a configured provider by setting the bootstrap
    fields.
    """
    from app.assistant import config_store

    config_store.invalidate_cache()
    yield
    config_store.invalidate_cache()


@pytest.fixture
def assistant_config():
    """Put a chosen assistant configuration in force for one test.

    The provider credential, the model, the timeouts and the history bounds are
    no longer environment variables: they come from a row the platform super
    admin owns, resolved through app.assistant.config_store. A test that wants
    "no credential" or "a two message ceiling" seeds the resolved value here
    rather than setting an environment variable that nothing reads any more.

    Seeding the cache rather than the row is deliberate: it needs no database,
    and it reaches every module at once, however each one imported get_config.
    """
    import time
    from dataclasses import replace

    from app.assistant import config_store

    def _use(**overrides):
        config = replace(config_store.AssistantRuntimeConfig(), **overrides)
        config_store._cached = config
        config_store._cached_at = time.monotonic()
        return config

    yield _use
    config_store.invalidate_cache()

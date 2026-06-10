import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Add the project root to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# If a shared Base exists for tenant DBs, import it here.
# For now, migrations are service-specific and each service manages its own tables.
# We use a minimal Base if available; otherwise target_metadata stays None.
try:
    from app.db.base import Base  # noqa: E402
    target_metadata = Base.metadata
except ImportError:
    target_metadata = None

config = context.config

# Override the sqlalchemy.url from the env var TENANT_DB_URL
# This is set dynamically when provisioning a new tenant database
# e.g., postgresql://postgres:nasr@localhost:5432/tenant_hosp-xxx
db_url = os.getenv("TENANT_DB_URL", "postgresql://postgres:postgres@localhost:5432/hospital_tenant")
config.set_main_option("sqlalchemy.url", db_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

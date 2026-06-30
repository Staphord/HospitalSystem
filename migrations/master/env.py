import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Add the project root to the path so we can import app.db.base
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# We expect a Base in app.db.base
# If it doesn't exist yet, the migration file is self-contained.
from app.db.base import Base  # noqa: E402

target_metadata = Base.metadata

# this is the Alembic Config object
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override the sqlalchemy.url from the env var DATABASE_URL (master database)
# The master database stores: tenants, users, super_admins, audit_logs, etc.
db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/hospital_master")
config.set_main_option("sqlalchemy.url", db_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_col_length=64,
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
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_col_length=64,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

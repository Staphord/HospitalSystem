"""Schema helpers shared by microservices.

Alembic owns production schema. Services used to call ``metadata.create_all``
on startup, which races across containers and crashes Postgres when a table or
composite type already exists. Skip create when the database already has tables.
Still add columns that exist on the model but not on the live table.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

logger = logging.getLogger(__name__)


def create_schema_if_missing(bind, metadata) -> None:
    try:
        inspector = inspect(bind)
        existing = set(inspector.get_table_names())
    except Exception:
        logger.warning("Could not inspect database schema; skipping create_all")
        return

    tables = [table for table in metadata.sorted_tables if table.name not in existing]
    if tables:
        try:
            metadata.create_all(bind=bind, tables=tables)
        except (IntegrityError, ProgrammingError, OperationalError) as exc:
            logger.warning("create_all skipped because schema already exists: %s", exc)
    else:
        logger.info("All mapped tables already exist; skipping create_all")

    _add_missing_columns(bind, metadata)


def _column_default_sql(column) -> str | None:
    if column.server_default is not None:
        arg = getattr(column.server_default, "arg", None)
        if isinstance(arg, str) and arg.strip():
            return arg
    default = column.default
    if default is None:
        return None
    arg = getattr(default, "arg", None)
    if arg is None or callable(arg):
        return None
    if isinstance(arg, bool):
        return "true" if arg else "false"
    if isinstance(arg, (int, float)):
        return str(arg)
    if isinstance(arg, str):
        return "'" + arg.replace("'", "''") + "'"
    return None


def _add_missing_columns(bind, metadata) -> None:
    try:
        inspector = inspect(bind)
        existing_tables = set(inspector.get_table_names())
    except Exception:
        logger.warning("Could not inspect columns; skipping ALTER TABLE")
        return

    dialect = bind.dialect
    added = 0
    with bind.connect() as conn:
        for table in metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            try:
                existing_cols = {col["name"] for col in inspector.get_columns(table.name)}
            except Exception:
                continue
            for column in table.columns:
                if column.name in existing_cols:
                    continue
                type_sql = column.type.compile(dialect=dialect)
                default_sql = _column_default_sql(column)
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {type_sql}'
                if default_sql is not None:
                    ddl += f" DEFAULT {default_sql}"
                if column.nullable or default_sql is None:
                    ddl += " NULL"
                else:
                    ddl += " NOT NULL"
                try:
                    conn.execute(text(ddl))
                    conn.commit()
                    added += 1
                    logger.info("Added missing column %s.%s", table.name, column.name)
                except (IntegrityError, ProgrammingError, OperationalError) as exc:
                    conn.rollback()
                    logger.warning(
                        "Could not add column %s.%s: %s", table.name, column.name, exc
                    )
    if added:
        logger.info("Added %s missing column(s)", added)

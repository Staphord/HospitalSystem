import logging
from typing import Optional

from sqlalchemy import MetaData, Table, inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger("visit_service.sync")


def _ensure_enum_types(engine: Engine, metadata: MetaData) -> None:
    """Create any missing enum types referenced by model columns (idempotent)."""
    from sqlalchemy import Enum as SAEnum

    with engine.begin() as conn:
        for table in metadata.tables.values():
            for column in table.columns:
                if isinstance(column.type, SAEnum):
                    enum_name = column.type.name
                    if enum_name:
                        values = ", ".join(f"'{v}'" for v in column.type.enums)
                        conn.execute(text(f"""
                            DO $$ BEGIN
                                CREATE TYPE {enum_name} AS ENUM ({values});
                            EXCEPTION WHEN duplicate_object THEN NULL;
                            END $$;
                        """))
                        logger.info("Ensured enum type %s", enum_name)


def _suppress_enum_ddl(metadata: MetaData) -> list:
    """Temporarily disable SQLAlchemy's auto-create for enum types. Returns list of (col, orig) to restore."""
    from sqlalchemy import Enum as SAEnum
    saved = []
    for table in metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, SAEnum) and hasattr(column.type, "_create_events"):
                saved.append((column.type, column.type._create_events))
                column.type._create_events = False
    return saved


def _restore_enum_ddl(saved: list) -> None:
    for enum_type, orig in saved:
        enum_type._create_events = orig


def sync_tenant_schema(engine: Engine, metadata: MetaData) -> None:
    _ensure_enum_types(engine, metadata)
    restored = _suppress_enum_ddl(metadata)
    try:
        inspector = inspect(engine)
        existing_tables = inspector.get_table_names()

        for table_name, table in metadata.tables.items():
            if table_name not in existing_tables:
                try:
                    table.create(engine)
                except Exception as exc:
                    if "already exists" in str(exc):
                        logger.warning("Ignored duplicate object error during table creation: %s", exc)
                    else:
                        raise
                logger.info("Created missing table %s", table_name)
                continue

        existing_columns = {col["name"] for col in inspector.get_columns(table_name)}
        missing_columns = []
        for column in table.columns:
            if column.name not in existing_columns:
                from sqlalchemy import Enum as SAEnum
                if isinstance(column.type, SAEnum):
                    col_type = str(column.type.name)
                else:
                    orig_ce = getattr(column.type, "_create_events", True)
                    if hasattr(column.type, "_create_events"):
                        column.type._create_events = False
                    try:
                        col_type = column.type.compile(engine.dialect)
                    finally:
                        if hasattr(column.type, "_create_events"):
                            column.type._create_events = orig_ce
                nullable = "NULL" if column.nullable else "NOT NULL"
                missing_columns.append(f"ADD COLUMN IF NOT EXISTS {column.name} {col_type} {nullable}")

        for idx in table.indexes:
            if idx.name and not any(
                ix["name"] == idx.name for ix in inspector.get_indexes(table_name)
            ):
                cols = ", ".join(c.name for c in idx.columns)
                unique = " UNIQUE" if idx.unique else ""
                try:
                    with engine.connect() as conn:
                        conn.execute(text(f"CREATE{unique} INDEX IF NOT EXISTS {idx.name} ON {table_name} ({cols})"))
                        conn.commit()
                except Exception:
                    pass

            if missing_columns:
                sql = f"ALTER TABLE {table_name} {', '.join(missing_columns)}"
                with engine.begin() as conn:
                    conn.execute(text(sql))
                logger.info("Added %d missing column(s) to %s: %s",
                            len(missing_columns), table_name,
                            [c.split()[-3] for c in missing_columns])
    finally:
        _restore_enum_ddl(restored)

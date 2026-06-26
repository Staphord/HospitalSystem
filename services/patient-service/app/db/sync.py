import logging
from typing import Optional

from sqlalchemy import MetaData, Table, inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger("patient_service.sync")


def _table_has_rows(engine: Engine, table_name: str) -> bool:
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
        return result.scalar() > 0


def sync_tenant_schema(engine: Engine, metadata: MetaData) -> None:
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()

    for table_name, table in metadata.tables.items():
        if table_name not in existing_tables:
            table.create(engine)
            logger.info("Created missing table %s", table_name)
            continue

        existing_columns = {col["name"] for col in inspector.get_columns(table_name)}
        missing_columns = []
        not_null_defaults = []

        for column in table.columns:
            if column.name not in existing_columns:
                col_type = column.type.compile(engine.dialect)
                # If column is NOT NULL and table has rows, add as nullable first,
                # then set NOT NULL after filling defaults
                if not column.nullable and _table_has_rows(engine, table_name):
                    missing_columns.append(f"ADD COLUMN IF NOT EXISTS {column.name} {col_type} NULL")
                    not_null_defaults.append(column.name)
                else:
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
                        [c.split(" ")[3] if " " in c else c for c in missing_columns])

        # Convert nullable columns to NOT NULL where the model requires it
        for col_name in not_null_defaults:
            from sqlalchemy import text as sa_text
            # Fill any remaining nulls with a default
            with engine.begin() as conn:
                conn.execute(sa_text(
                    f"UPDATE {table_name} SET {col_name} = '' WHERE {col_name} IS NULL"
                ))
                dialect = engine.dialect.name
                if dialect == "postgresql":
                    conn.execute(sa_text(
                        f"ALTER TABLE {table_name} ALTER COLUMN {col_name} SET NOT NULL"
                    ))
                else:
                    conn.execute(sa_text(
                        f"ALTER TABLE {table_name} MODIFY {col_name} VARCHAR NOT NULL"
                    ))
            logger.info("Set NOT NULL on column %s.%s", table_name, col_name)

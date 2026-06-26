import logging
from typing import Optional

from sqlalchemy import MetaData, Table, inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger("visit_service.sync")


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
        for column in table.columns:
            if column.name not in existing_columns:
                col_type = column.type.compile(engine.dialect)
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

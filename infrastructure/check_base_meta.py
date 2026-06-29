"""Check Base.metadata tables and enum columns."""
import app.models.visit
from app.db.base import Base
from sqlalchemy import Enum as SAEnum

print("Tables:", list(Base.metadata.tables.keys()))
for tname, table in Base.metadata.tables.items():
    for col in table.columns:
        is_enum = isinstance(col.type, SAEnum)
        ce = getattr(col.type, "_create_events", "N/A")
        print(f"{tname}.{col.name}: type={type(col.type).__name__} isEnum={is_enum} _create_events={ce}")

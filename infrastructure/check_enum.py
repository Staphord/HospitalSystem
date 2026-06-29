"""Check SQLAlchemy Enum type attributes."""
from sqlalchemy import Enum

e = Enum("a", "b", name="test_enum")
print("type:", type(e))
print("has create_type:", hasattr(type(e), "create_type"))
for attr in dir(e):
    low = attr.lower()
    if "create" in low or "ddl" in low:
        print(f"  {attr}: {getattr(e, attr, 'N/A')}")

from sqlalchemy import create_engine, text
from app.core.config import settings

e = create_engine(settings.database_url)
with e.connect() as conn:
    rows = conn.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name")
    ).fetchall()
    print("Tables in database:")
    for r in rows:
        print(f"  - {r[0]}")

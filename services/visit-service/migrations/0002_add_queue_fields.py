from sqlalchemy import create_engine, text


def run_migration(engine_url: str) -> None:
    engine = create_engine(engine_url)
    with engine.connect() as conn:
        conn.execute(text("""
            ALTER TABLE patient_insurance
            ADD COLUMN IF NOT EXISTS verified_at TIMESTAMP
        """))
        conn.execute(text("""
            ALTER TABLE queues
            ADD COLUMN IF NOT EXISTS called_at TIMESTAMP
        """))
        conn.execute(text("""
            ALTER TABLE queues
            ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP
        """))
        conn.commit()
    engine.dispose()


if __name__ == "__main__":
    import os
    db_url = os.environ.get("DATABASE_URL", "postgresql://user:pass@localhost:5432/tenant_default")
    run_migration(db_url)

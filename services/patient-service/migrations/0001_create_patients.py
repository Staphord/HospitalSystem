from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


def run_migration(engine_url: str) -> None:
    engine = create_engine(engine_url)
    with engine.connect() as conn:
        conn.execute(text("""
            DO $$ BEGIN
                CREATE TYPE patient_gender_enum AS ENUM ('male','female','other');
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS patients (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                hospital_id VARCHAR(50) NOT NULL,
                patient_number VARCHAR(20) NOT NULL,
                full_name VARCHAR(200) NOT NULL,
                date_of_birth DATE NOT NULL,
                gender patient_gender_enum NOT NULL,
                phone_primary VARCHAR(20) NOT NULL,
                phone_secondary VARCHAR(20),
                email VARCHAR(150),
                address TEXT,
                next_of_kin_name VARCHAR(200),
                next_of_kin_phone VARCHAR(20),
                next_of_kin_relationship VARCHAR(50),
                national_id VARCHAR(50),
                allergies TEXT,
                blood_group VARCHAR(5),
                is_active BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMP NOT NULL DEFAULT now(),
                updated_at TIMESTAMP NOT NULL DEFAULT now(),
                created_by VARCHAR(36)
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_patients_hospital_id ON patients(hospital_id)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_patients_patient_number ON patients(patient_number)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_patients_full_name ON patients(full_name)
        """))
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_patients_hospital_patient_number
            ON patients(hospital_id, patient_number)
        """))
        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_patients_hospital_national_id
            ON patients(hospital_id, national_id)
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS patient_number_sequences (
                id SERIAL PRIMARY KEY,
                hospital_id VARCHAR(50) NOT NULL,
                date_key VARCHAR(8) NOT NULL,
                counter INTEGER NOT NULL DEFAULT 0,
                UNIQUE(hospital_id, date_key)
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_sequences_hospital_date
            ON patient_number_sequences(hospital_id, date_key)
        """))
        conn.commit()
    engine.dispose()


if __name__ == "__main__":
    import os
    db_url = os.environ.get("DATABASE_URL", "postgresql://user:pass@localhost:5432/tenant_default")
    run_migration(db_url)

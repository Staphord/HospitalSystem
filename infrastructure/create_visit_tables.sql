DO $$ BEGIN
    CREATE TYPE visit_type_enum AS ENUM ('outpatient','inpatient','emergency');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE payment_type_enum AS ENUM ('cash','insurance');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE visit_status_enum AS ENUM ('registered','triaged','in_consultation','in_lab','in_pharmacy','completed','cancelled');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE queue_type_enum AS ENUM ('triage','doctor','lab','radiology','pharmacy','billing');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE priority_enum AS ENUM ('emergency','urgent','semi_urgent','non_urgent');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE queue_status_enum AS ENUM ('waiting','in_progress','completed','skipped');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE verification_status_enum AS ENUM ('pending','verified','rejected');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS visits (
    visit_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id UUID NOT NULL,
    visit_number VARCHAR(20) NOT NULL UNIQUE,
    visit_date DATE NOT NULL DEFAULT CURRENT_DATE,
    visit_type visit_type_enum NOT NULL,
    payment_type payment_type_enum NOT NULL,
    insurance_id UUID REFERENCES patient_insurance(insurance_id),
    verification_flag TEXT,
    queue_number VARCHAR(10),
    status visit_status_enum NOT NULL DEFAULT 'registered',
    registered_by UUID NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_visits_patient ON visits(patient_id);
CREATE INDEX IF NOT EXISTS idx_visits_number ON visits(visit_number);

CREATE TABLE IF NOT EXISTS queues (
    queue_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    visit_id UUID NOT NULL REFERENCES visits(visit_id),
    patient_id UUID NOT NULL,
    queue_type queue_type_enum NOT NULL,
    queue_number VARCHAR(10) NOT NULL,
    priority priority_enum NOT NULL DEFAULT 'non_urgent',
    status queue_status_enum NOT NULL DEFAULT 'waiting',
    created_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_queues_visit ON queues(visit_id);

CREATE TABLE IF NOT EXISTS visit_number_sequences (
    id SERIAL PRIMARY KEY,
    date_key VARCHAR(8) NOT NULL UNIQUE,
    counter INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS queue_number_sequences (
    id SERIAL PRIMARY KEY,
    queue_type VARCHAR(20) NOT NULL,
    date_key VARCHAR(8) NOT NULL,
    counter INTEGER NOT NULL DEFAULT 0,
    UNIQUE(queue_type, date_key)
);

-- Add verification_status to patient_insurance if missing
ALTER TABLE patient_insurance ADD COLUMN IF NOT EXISTS verification_status verification_status_enum NOT NULL DEFAULT 'pending';
ALTER TABLE patient_insurance ADD COLUMN IF NOT EXISTS verified_at TIMESTAMP;

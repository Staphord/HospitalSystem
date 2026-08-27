"""Test setup for the clinical decision support service.

Settings are populated from the environment before anything imports the app, so
`pytest -q` works in this directory without a stack running. The values are
inert placeholders: no database is reached, no Keycloak is reached, and no
outbound call is made anywhere in this suite.
"""

import os

_TEST_ENV = {
    "ENVIRONMENT": "test",
    "DATABASE_URL": "postgresql://user:pass@localhost:5432/hospital_master",
    "SECRET_KEY": "test-secret-key",
    "REDIS_URL": "redis://localhost:6379/0",
    "KEYCLOAK_URL": "http://localhost:8080",
    "KEYCLOAK_REALM": "hospital",
    "KEYCLOAK_CLIENT_ID": "hospital-backend",
    "KEYCLOAK_CLIENT_SECRET": "test-client-secret",
    "KEYCLOAK_ADMIN_USERNAME": "admin",
    "KEYCLOAK_ADMIN_PASSWORD": "admin",
    "KEYCLOAK_INTROSPECT": "false",
    "ALLOWED_ORIGINS": "http://localhost:3000",
    "DEFAULT_HOSPITAL_ID": "default-hospital",
    "TENANT_DB_ENCRYPTION_KEY": "test-tenant-db-encryption-key",
}

for _key, _value in _TEST_ENV.items():
    os.environ.setdefault(_key, _value)

import asyncio  # noqa: E402
from datetime import date, datetime, timezone  # noqa: E402
from uuid import UUID  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.cds.flags import CdsCapability  # noqa: E402
from app.core.tenant_auth import TenantContext, get_current_tenant  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.dependencies import get_tenant_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.cds import CdsAlertAction, CdsMedicationCheck  # noqa: E402

TENANT_ID = "hosp-c5c8388b"
OTHER_TENANT_ID = "hosp-99999999"

VISIT_ID = UUID("b2000002-0002-4002-8002-000000000002")
CANCELLED_VISIT_ID = UUID("b2000002-0002-4002-8002-000000000009")
PATIENT_ID = UUID("c3000003-0003-4003-8003-000000000003")
PATIENT_NO_ALLERGY_RECORD_ID = UUID("c3000003-0003-4003-8003-000000000004")
PRESCRIPTION_ID = UUID("e2000002-0002-4002-8002-000000000002")

# The tenant tables this service reads belong to other services. The suite
# creates them directly rather than importing another service's models, so a
# schema change there shows up here as a test failure rather than as an import.
TENANT_FIXTURE_DDL = (
    """
    CREATE TABLE visits (
        visit_id CHAR(36) PRIMARY KEY,
        patient_id CHAR(36) NOT NULL,
        visit_number VARCHAR(20),
        visit_date DATE,
        visit_type VARCHAR(50),
        payment_type VARCHAR(50),
        status VARCHAR(50),
        billing_cleared BOOLEAN
    )
    """,
    """
    CREATE TABLE patients (
        id CHAR(36) PRIMARY KEY,
        hospital_id VARCHAR(50),
        patient_number VARCHAR(30),
        full_name VARCHAR(200),
        date_of_birth DATE,
        gender VARCHAR(20),
        allergies TEXT
    )
    """,
    """
    CREATE TABLE pharmacy_prescriptions (
        prescription_id CHAR(36) PRIMARY KEY,
        visit_id CHAR(36) NOT NULL,
        patient_id CHAR(36) NOT NULL,
        prescribed_by VARCHAR(255),
        prescribed_at TIMESTAMP,
        status VARCHAR(50)
    )
    """,
    """
    CREATE TABLE pharmacy_prescription_items (
        prescription_item_id CHAR(36) PRIMARY KEY,
        prescription_id CHAR(36) NOT NULL,
        drug_name VARCHAR(200),
        dose VARCHAR(100),
        frequency VARCHAR(100),
        duration VARCHAR(100),
        instructions TEXT,
        quantity_prescribed INTEGER,
        status VARCHAR(50)
    )
    """,
    """
    CREATE TABLE drug_inventory (
        inventory_id CHAR(36) PRIMARY KEY,
        drug_name VARCHAR(200) NOT NULL,
        brand_name VARCHAR(200),
        drug_code VARCHAR(50),
        category VARCHAR(100),
        unit VARCHAR(50),
        quantity_in_stock INTEGER,
        is_active BOOLEAN
    )
    """,
)

PRESCRIPTION_ITEM_ID = UUID("d4000004-0004-4004-8004-000000000004")

TENANT_FIXTURE_ROWS = (
    (
        "INSERT INTO drug_inventory (inventory_id, drug_name, brand_name, drug_code, "
        "category, unit, quantity_in_stock, is_active) VALUES "
        "('11111111-1111-4111-8111-111111111111', 'Warfarin', 'Coumadin', 'WAR-5', "
        "'Anticoagulant', 'tablets', 100, 1),"
        "('22222222-2222-4222-8222-222222222222', 'Ibuprofen', 'Brufen', 'IBU-400', "
        "'NSAID', 'tablets', 100, 1),"
        "('33333333-3333-4333-8333-333333333333', 'Amoxicillin', 'Amoxil', 'AMX-500', "
        "'Antibiotic', 'tablets', 100, 1),"
        "('44444444-4444-4444-8444-444444444444', 'Paracetamol', 'Panadol', 'PCM-500', "
        "'Analgesic', 'tablets', 100, 1),"
        "('55555555-5555-4555-8555-555555555555', 'Paracetamol Extra', 'Panadol Extra', "
        "'PCM-EX', 'Analgesic', 'tablets', 100, 1),"
        "('66666666-6666-4666-8666-666666666666', 'Diclofenac', 'Voltaren', 'DIC-50', "
        "'NSAID', 'tablets', 100, 1)"
    ),
    (
        "INSERT INTO patients (id, hospital_id, patient_number, full_name, date_of_birth, "
        "gender, allergies) VALUES "
        f"('{PATIENT_ID}', 'hosp-c5c8388b', 'PT-4891', 'Jane Mwita', '1985-04-12', "
        "'Female', 'Penicillin'),"
        f"('{PATIENT_NO_ALLERGY_RECORD_ID}', 'hosp-c5c8388b', 'PT-4892', 'Ali Juma', "
        "'1990-01-01', 'Male', NULL)"
    ),
    (
        "INSERT INTO visits (visit_id, patient_id, visit_number, visit_date, visit_type, "
        "payment_type, status, billing_cleared) VALUES "
        f"('{VISIT_ID}', '{PATIENT_ID}', 'V-1', '2026-08-27', 'outpatient', 'cash', "
        "'in_pharmacy', 1),"
        f"('{CANCELLED_VISIT_ID}', '{PATIENT_ID}', 'V-2', '2026-08-27', 'outpatient', "
        "'cash', 'cancelled', 0)"
    ),
    (
        "INSERT INTO pharmacy_prescriptions (prescription_id, visit_id, patient_id, "
        "prescribed_by, status) VALUES "
        f"('{PRESCRIPTION_ID}', '{VISIT_ID}', '{PATIENT_ID}', 'Dr Test', 'pending')"
    ),
    (
        "INSERT INTO pharmacy_prescription_items (prescription_item_id, prescription_id, "
        "drug_name, dose, frequency, duration, quantity_prescribed, status) VALUES "
        f"('{PRESCRIPTION_ITEM_ID}', '{PRESCRIPTION_ID}', 'Warfarin 5mg tablet', '5mg', "
        "'Once daily', '7 days', 7, 'pending')"
    ),
)


def _doctor_ctx(**overrides) -> TenantContext:
    values = {
        "tenant_id": TENANT_ID,
        "user_sub": "doctor-sub-1",
        "preferred_username": "qa_doctor",
        "email": None,
        "roles": ["doctor"],
        "is_super_admin": False,
        "scope": "full",
        "raw_token": {},
    }
    values.update(overrides)
    return TenantContext(**values)


@pytest.fixture(scope="session")
def tenant_engine():
    async def _setup():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            for statement in TENANT_FIXTURE_DDL:
                await conn.execute(text(statement))
            for statement in TENANT_FIXTURE_ROWS:
                await conn.execute(text(statement))
        return engine

    engine = asyncio.run(_setup())
    yield engine
    asyncio.run(engine.dispose())


@pytest.fixture
def session_factory(tenant_engine):
    return async_sessionmaker(tenant_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
def enabled(monkeypatch):
    """Switch the service and the medication capability on for one test."""
    from app.core import config

    monkeypatch.setattr(config.settings, "cds_enabled", True, raising=False)
    monkeypatch.setattr(config.settings, "cds_medication_check_enabled", True, raising=False)
    return CdsCapability.MEDICATION_CHECK


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    """Give each test its own rate-limit budget.

    The production limits stay exactly as configured; they are simply not
    carried from one test into the next, which would otherwise make a test fail
    because of how many tests ran before it.
    """
    from app.core.limiter import limiter

    if hasattr(limiter, "reset"):
        limiter.reset()
    yield
    if hasattr(limiter, "reset"):
        limiter.reset()


@pytest.fixture
def client(session_factory):
    """A signed-in client. Defaults to a doctor in the QA tenant."""

    async def override_db():
        async with session_factory() as session:
            yield session

    def _sign_in(**overrides):
        ctx = _doctor_ctx(**overrides)
        app.dependency_overrides[get_current_tenant] = lambda: ctx
        app.dependency_overrides[get_tenant_db] = override_db
        return TestClient(app)

    yield _sign_in
    app.dependency_overrides.clear()


__all__ = [
    "CANCELLED_VISIT_ID",
    "CdsAlertAction",
    "CdsMedicationCheck",
    "OTHER_TENANT_ID",
    "PATIENT_ID",
    "PATIENT_NO_ALLERGY_RECORD_ID",
    "TENANT_ID",
    "VISIT_ID",
    "date",
    "datetime",
    "timezone",
]

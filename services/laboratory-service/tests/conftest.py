import asyncio
import unittest.mock

# Mock init_db to avoid connecting to Postgres during test initialization
import app.core.database
app.core.database.init_db = unittest.mock.MagicMock()

# Mock event subscriber to avoid connection to RabbitMQ / aio_pika
import app.events.subscriber
async def mock_start_subscriber():
    pass
app.events.subscriber.start_subscriber = mock_start_subscriber

import pytest
from datetime import date, datetime, timezone
from uuid import UUID
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import TokenPayload, get_current_active_user
from app.core.tenant_auth import TenantContext, get_current_tenant
from app.db.base import Base
from app.dependencies import get_tenant_db
from app.main import app
from app.models.laboratory import Patient, Visit, InvestigationRequest, Queue

# Stable UUIDs for tests
TEST_PATIENT_ID = UUID("c3000003-0003-4003-8003-000000000003")
TEST_VISIT_ID = UUID("b2000002-0002-4002-8002-000000000002")
TEST_REQUEST_PENDING_ID = UUID("d4000004-0004-4004-8004-000000000004")
TEST_REQUEST_COMPLETED_ID = UUID("d4000004-0004-4004-8004-000000000005")
TEST_QUEUE_ID = UUID("a1000001-0001-4001-8001-000000000001")

LAB_TECH_USER = TokenPayload(
    sub="test-lab-tech-sub",
    preferred_username="labtech.test",
    email="labtech@test.com",
    realm_access={"roles": ["lab_technician"]},
    raw={"sub": "test-lab-tech-sub", "realm_access": {"roles": ["lab_technician"]}},
)

DOCTOR_USER = TokenPayload(
    sub="test-doctor-sub",
    preferred_username="doctor.test",
    email="doctor@test.com",
    realm_access={"roles": ["doctor"]},
    raw={"sub": "test-doctor-sub", "realm_access": {"roles": ["doctor"]}},
)

UNAUTHORIZED_USER = TokenPayload(
    sub="test-unauth-sub",
    preferred_username="unauth.test",
    email="unauth@test.com",
    realm_access={"roles": ["receptionist"]},
    raw={"sub": "test-unauth-sub", "realm_access": {"roles": ["receptionist"]}},
)

TENANT_CTX = TenantContext(
    tenant_id="default-hospital",
    user_sub="test-lab-tech-sub",
    preferred_username="labtech.test",
    email="labtech@test.com",
    roles=["lab_technician"],
    is_super_admin=False,
)


@pytest.fixture(scope="session")
def test_engine():
    async def _setup():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            # Add seed Patient
            pat = Patient(
                id=TEST_PATIENT_ID,
                full_name="Jane Mwita",
                date_of_birth=date(1985, 4, 12),
                gender="female",
                allergies="Penicillin",
            )
            # Add seed Visit
            vis = Visit(
                visit_id=TEST_VISIT_ID,
                patient_id=TEST_PATIENT_ID,
                visit_number="V-20260315-042",
                status="registered",
            )
            # Add seed InvestigationRequests
            req1 = InvestigationRequest(
                id=TEST_REQUEST_PENDING_ID,
                consultation_id=UUID("e5000005-0005-4005-8005-000000000005"),
                visit_id=TEST_VISIT_ID,
                patient_id=TEST_PATIENT_ID,
                request_type="laboratory",
                test_name="Full Blood Count",
                clinical_history="Fever and malaise",
                status="pending",
                created_by="Dr. Nguyen",
            )
            req2 = InvestigationRequest(
                id=TEST_REQUEST_COMPLETED_ID,
                consultation_id=UUID("e5000005-0005-4005-8005-000000000005"),
                visit_id=TEST_VISIT_ID,
                patient_id=TEST_PATIENT_ID,
                request_type="laboratory",
                test_name="Malaria BS",
                clinical_history="High grade fever",
                status="completed",
                created_by="Dr. Nguyen",
            )
            # Add seed Queue
            q = Queue(
                queue_id=TEST_QUEUE_ID,
                visit_id=TEST_VISIT_ID,
                patient_id=TEST_PATIENT_ID,
                queue_type="lab",
                queue_number="PH-007",
                priority="urgent",
                status="waiting",
            )

            session.add_all([pat, vis, req1, req2, q])
            await session.commit()

        return engine

    engine = asyncio.run(_setup())
    yield engine
    asyncio.run(engine.dispose())


@pytest.fixture
def db_session(test_engine):
    session_factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    session = asyncio.run(session_factory().__aenter__())
    yield session
    asyncio.run(session.__aexit__(None, None, None))


@pytest.fixture
def lab_tech_client(test_engine):
    session_factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def override_db():
        async with session_factory() as session:
            yield session

    async def override_tenant():
        return TENANT_CTX

    async def override_user():
        return LAB_TECH_USER

    app.dependency_overrides[get_current_tenant] = override_tenant
    app.dependency_overrides[get_current_active_user] = override_user
    app.dependency_overrides[get_tenant_db] = override_db
    
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def doctor_client(test_engine):
    session_factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def override_db():
        async with session_factory() as session:
            yield session

    async def override_tenant():
        return TenantContext(
            tenant_id="default-hospital",
            user_sub="test-doctor-sub",
            preferred_username="doctor.test",
            email="doctor@test.com",
            roles=["doctor"],
            is_super_admin=False,
        )

    async def override_user():
        return DOCTOR_USER

    app.dependency_overrides[get_current_tenant] = override_tenant
    app.dependency_overrides[get_current_active_user] = override_user
    app.dependency_overrides[get_tenant_db] = override_db
    
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def unauthorized_client(test_engine):
    session_factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def override_db():
        async with session_factory() as session:
            yield session

    async def override_tenant():
        return TenantContext(
            tenant_id="default-hospital",
            user_sub="test-unauth-sub",
            preferred_username="unauth.test",
            email="unauth@test.com",
            roles=["receptionist"],
            is_super_admin=False,
        )

    async def override_user():
        return UNAUTHORIZED_USER

    app.dependency_overrides[get_current_tenant] = override_tenant
    app.dependency_overrides[get_current_active_user] = override_user
    app.dependency_overrides[get_tenant_db] = override_db
    
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()

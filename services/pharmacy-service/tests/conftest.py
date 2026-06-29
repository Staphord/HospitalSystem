import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import TokenPayload, get_current_active_user
from app.core.tenant_auth import TenantContext, get_current_tenant
from app.db.base import Base
from app.dependencies import get_tenant_db
from app.main import app
from app.models.pharmacy import DrugInventory
from app.services.inventory import (
    SEED_INVENTORY_AMOXICILLIN_ID,
    SEED_INVENTORY_METRONIDAZOLE_ID,
)

PHARMACIST_USER = TokenPayload(
    sub="test-pharmacist-sub",
    preferred_username="pharmacist.test",
    email="pharmacist@test.com",
    realm_access={"roles": ["pharmacist"]},
    raw={"sub": "test-pharmacist-sub", "realm_access": {"roles": ["pharmacist"]}},
)

TENANT_CTX = TenantContext(
    tenant_id="default-hospital",
    user_sub="test-pharmacist-sub",
    preferred_username="pharmacist.test",
    email="pharmacist@test.com",
    roles=["pharmacist"],
    is_super_admin=False,
)


async def _override_tenant() -> TenantContext:
    return TENANT_CTX


async def _override_user() -> TokenPayload:
    return PHARMACIST_USER


@pytest.fixture(scope="session")
def test_engine():
    async def _setup():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            session.add_all(
                [
                    DrugInventory(
                        inventory_id=SEED_INVENTORY_AMOXICILLIN_ID,
                        drug_name="Amoxicillin",
                        brand_name="Amoxil",
                        drug_code="AMX-500",
                        category="Antibiotic",
                        unit="tablets",
                        quantity_in_stock=179,
                        reorder_level=100,
                        unit_cost=50.00,
                        unit_price=80.00,
                        location="Shelf B-3",
                        is_active=True,
                    ),
                    DrugInventory(
                        inventory_id=SEED_INVENTORY_METRONIDAZOLE_ID,
                        drug_name="Metronidazole",
                        brand_name="Flagyl",
                        drug_code="MTZ-400",
                        category="Antibiotic",
                        unit="tablets",
                        quantity_in_stock=12,
                        reorder_level=50,
                        unit_cost=30.00,
                        unit_price=55.00,
                        location="Shelf C-1",
                        is_active=True,
                    ),
                ]
            )
            await session.commit()

        return engine

    engine = asyncio.run(_setup())
    yield engine
    asyncio.run(engine.dispose())


@pytest.fixture
def pharmacist_client(test_engine):
    session_factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def override_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_current_tenant] = _override_tenant
    app.dependency_overrides[get_current_active_user] = _override_user
    app.dependency_overrides[get_tenant_db] = override_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()

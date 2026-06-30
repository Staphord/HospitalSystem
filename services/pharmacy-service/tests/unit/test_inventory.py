import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.schemas import AdjustInventoryRequest
from app.core.security import TokenPayload
from app.db.base import Base
from app.exceptions import ConflictError
from app.models.pharmacy import DrugInventory
from app.services import inventory as inventory_service

PHARMACIST = TokenPayload(
    sub="sub",
    preferred_username="amina",
    email=None,
    realm_access={"roles": ["pharmacist"]},
    raw={},
)


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        session.add(
            DrugInventory(
                inventory_id=inventory_service.SEED_INVENTORY_AMOXICILLIN_ID,
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
            )
        )
        await session.commit()
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_list_inventory_returns_seeded_item(db_session: AsyncSession):
    result = await inventory_service.list_inventory(db_session, None, None, None, 1, 50)
    assert result.total == 1
    assert result.items[0].drug_name == "Amoxicillin"


@pytest.mark.asyncio
async def test_adjust_inventory_negative_stock_raises_409(db_session: AsyncSession):
    body = AdjustInventoryRequest(
        inventory_id=inventory_service.SEED_INVENTORY_AMOXICILLIN_ID,
        transaction_type="write_off",
        quantity_change=-500,
        notes="Expired batch",
    )
    with pytest.raises(ConflictError) as exc:
        await inventory_service.adjust_inventory(db_session, body, PHARMACIST)
    assert "STOCK_CANNOT_GO_NEGATIVE" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_restock_increases_stock(db_session: AsyncSession):
    from datetime import date

    from app.api.v1.schemas import RestockRequest

    body = RestockRequest(
        inventory_id=inventory_service.SEED_INVENTORY_AMOXICILLIN_ID,
        quantity_added=100,
        batch_number="BATCH-TEST",
        expiry_date=date(2028, 12, 31),
        unit_cost=48.0,
        notes="Test restock",
    )
    result = await inventory_service.restock_inventory(db_session, body, PHARMACIST)
    assert result.quantity_before == 179
    assert result.quantity_after == 279

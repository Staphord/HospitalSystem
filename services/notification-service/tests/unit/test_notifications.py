from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.schemas import NotificationCreateRequest, UpdatePreferenceRequest
from app.db.base import Base
from app.exceptions import NotFoundError
from app.services import notifications as svc

TEST_TENANT_ID = "tenant-001"
TEST_USER_ID = "user-123"
TEST_USER_ROLE = "doctor"


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_create_and_get_user_notifications(db_session: AsyncSession):
    req = NotificationCreateRequest(
        tenant_id=TEST_TENANT_ID,
        recipient_id=TEST_USER_ID,
        recipient_role=TEST_USER_ROLE,
        title="Critical Lab Result",
        message="Patient John Doe lab result ready.",
        category="clinical",
        priority="urgent",
        action_url="/laboratory/requests/123",
        metadata_payload={"patient_id": "p-1"},
    )
    created = await svc.create_notification(db_session, req)
    assert created.title == "Critical Lab Result"
    assert created.status == "unread"

    res = await svc.get_user_notifications(
        db_session,
        tenant_id=TEST_TENANT_ID,
        recipient_id=TEST_USER_ID,
        recipient_role=TEST_USER_ROLE,
    )
    assert res.total == 1
    assert res.unread_count == 1
    assert res.items[0].notification_id == created.notification_id


@pytest.mark.asyncio
async def test_unread_count(db_session: AsyncSession):
    req1 = NotificationCreateRequest(
        tenant_id=TEST_TENANT_ID,
        recipient_id=TEST_USER_ID,
        title="Alert 1",
        message="Message 1",
        category="system",
    )
    req2 = NotificationCreateRequest(
        tenant_id=TEST_TENANT_ID,
        recipient_id=TEST_USER_ID,
        title="Alert 2",
        message="Message 2",
        category="pharmacy",
    )
    await svc.create_notification(db_session, req1)
    await svc.create_notification(db_session, req2)

    unread = await svc.get_unread_count(
        db_session,
        tenant_id=TEST_TENANT_ID,
        recipient_id=TEST_USER_ID,
    )
    assert unread.unread_count == 2


@pytest.mark.asyncio
async def test_mark_single_notification_read(db_session: AsyncSession):
    req = NotificationCreateRequest(
        tenant_id=TEST_TENANT_ID,
        recipient_id=TEST_USER_ID,
        title="Test Alert",
        message="Test Content",
        category="billing",
    )
    created = await svc.create_notification(db_session, req)

    res = await svc.mark_notification_read(
        db_session,
        tenant_id=TEST_TENANT_ID,
        notification_id=created.notification_id,
    )
    assert res.marked_count == 1

    unread = await svc.get_unread_count(
        db_session,
        tenant_id=TEST_TENANT_ID,
        recipient_id=TEST_USER_ID,
    )
    assert unread.unread_count == 0


@pytest.mark.asyncio
async def test_mark_all_notifications_read(db_session: AsyncSession):
    for i in range(3):
        req = NotificationCreateRequest(
            tenant_id=TEST_TENANT_ID,
            recipient_id=TEST_USER_ID,
            title=f"Alert {i}",
            message=f"Message {i}",
            category="clinical",
        )
        await svc.create_notification(db_session, req)

    res = await svc.mark_all_notifications_read(
        db_session,
        tenant_id=TEST_TENANT_ID,
        recipient_id=TEST_USER_ID,
    )
    assert res.marked_count == 3

    unread = await svc.get_unread_count(
        db_session,
        tenant_id=TEST_TENANT_ID,
        recipient_id=TEST_USER_ID,
    )
    assert unread.unread_count == 0


@pytest.mark.asyncio
async def test_user_preferences_default_and_update(db_session: AsyncSession):
    pref = await svc.get_user_preferences(db_session, user_id=TEST_USER_ID)
    assert pref.in_app_enabled is True
    assert pref.sms_enabled is False

    update_payload = UpdatePreferenceRequest(sms_enabled=True, categories_disabled=["billing"])
    updated = await svc.update_user_preferences(
        db_session, user_id=TEST_USER_ID, payload=update_payload
    )
    assert updated.sms_enabled is True
    assert "billing" in updated.categories_disabled

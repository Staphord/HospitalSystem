from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.services import notifications as notif_srv
from app.api.v1.schemas import (
    NotificationCreateRequest,
    NotificationItemResponse,
    UpdatePreferenceRequest,
    NotificationListResponse,
    UnreadCountResponse,
    MarkReadResponse,
    NotificationPreferenceResponse,
)
from app.exceptions import NotFoundError
from app.core.tenant_auth import get_current_tenant, TenantContext
from app.dependencies import get_tenant_db


class TestNotificationRouterEndpoints:
    def setup_method(self):
        self.user_id = "user-notif-1"
        self.mock_tenant = TenantContext(
            tenant_id="t1",
            user_sub=self.user_id,
            preferred_username="notif_user",
            email="notif@hosp.org",
            roles=["doctor"],
            is_super_admin=False,
        )

        app.dependency_overrides[get_current_tenant] = lambda: self.mock_tenant

        mock_db = AsyncMock()
        app.dependency_overrides[get_tenant_db] = lambda: mock_db
        self.mock_db = mock_db
        self.client = TestClient(app)

    def teardown_method(self):
        app.dependency_overrides.clear()

    def test_list_notifications_endpoint(self):
        mock_resp = NotificationListResponse(items=[], total=0, page=1, page_size=50, unread_count=0)
        with patch("app.services.notifications.get_user_notifications", new=AsyncMock(return_value=mock_resp)):
            resp = self.client.get("/api/v1/notifications")
            assert resp.status_code == 200
            assert resp.json()["total"] == 0

    def test_get_unread_count_endpoint(self):
        mock_resp = UnreadCountResponse(unread_count=3)
        with patch("app.services.notifications.get_unread_count", new=AsyncMock(return_value=mock_resp)):
            resp = self.client.get("/api/v1/notifications/unread-count")
            assert resp.status_code == 200
            assert resp.json()["unread_count"] == 3

    def test_mark_single_read_endpoint(self):
        nid = uuid4()
        mock_resp = MarkReadResponse(notification_id=nid, marked_count=1, status="read")
        with patch("app.services.notifications.mark_notification_read", new=AsyncMock(return_value=mock_resp)):
            resp = self.client.patch(f"/api/v1/notifications/{nid}/read")
            assert resp.status_code == 200
            assert resp.json()["marked_count"] == 1

    def test_mark_all_read_endpoint(self):
        mock_resp = MarkReadResponse(notification_id=None, marked_count=4, status="read")
        with patch("app.services.notifications.mark_all_notifications_read", new=AsyncMock(return_value=mock_resp)):
            resp = self.client.post("/api/v1/notifications/mark-all-read")
            assert resp.status_code == 200
            assert resp.json()["marked_count"] == 4

    def test_get_and_update_preferences_endpoints(self):
        mock_pref = NotificationPreferenceResponse(user_id="u1", email_enabled=True, in_app_enabled=True, sms_enabled=False, categories_disabled=[])
        with patch("app.services.notifications.get_user_preferences", new=AsyncMock(return_value=mock_pref)):
            resp_get = self.client.get("/api/v1/notifications/preferences")
            assert resp_get.status_code == 200

        with patch("app.services.notifications.update_user_preferences", new=AsyncMock(return_value=mock_pref)):
            resp_put = self.client.put("/api/v1/notifications/preferences", json={"email_enabled": False, "in_app_enabled": True, "sms_enabled": False, "categories_disabled": []})
            assert resp_put.status_code == 200

    def test_stream_notifications_endpoint(self):
        import json, asyncio
        mock_queue = AsyncMock()
        mock_queue.get.side_effect = [json.dumps({"msg": "hi"}), asyncio.CancelledError()]
        with patch("app.services.broadcaster.broadcaster.subscribe", return_value=mock_queue):
            with patch("app.services.broadcaster.broadcaster.unsubscribe"):
                resp = self.client.get("/api/v1/notifications/stream")
                assert resp.status_code == 200

    def test_create_notification_endpoint(self):
        from uuid import uuid4
        payload = {
            "tenant_id": "t1",
            "recipient_id": "u1",
            "recipient_role": "doctor",
            "title": "Alert",
            "message": "Patient ready",
            "category": "clinical",
            "priority": "normal",
        }
        mock_item = NotificationItemResponse(
            notification_id=uuid4(),
            tenant_id="t1",
            recipient_id="u1",
            recipient_role="doctor",
            title="Alert",
            message="Patient ready",
            category="clinical",
            priority="normal",
            status="unread",
            action_url=None,
            metadata_payload={},
            created_at=datetime.now(timezone.utc),
            read_at=None,
        )
        with patch("app.services.notifications.create_notification", new=AsyncMock(return_value=mock_item)):
            with patch("app.services.broadcaster.broadcaster.broadcast", new=AsyncMock()):
                resp = self.client.post("/api/v1/notifications", json=payload)
                assert resp.status_code == 201


@pytest.mark.asyncio
async def test_get_unread_count_str_role_and_already_read():
    from uuid import uuid4
    from app.services import notifications as notif_service
    from app.api.v1.schemas import UpdatePreferenceRequest

    mock_db = AsyncMock()
    mock_pref_res = MagicMock()
    mock_pref_res.scalar_one_or_none.return_value = None
    mock_count_res = MagicMock()
    mock_count_res.scalar.return_value = 5
    mock_db.execute.side_effect = [mock_pref_res, mock_count_res]

    res = await notif_service.get_unread_count(mock_db, "t1", "user-1", "doctor")
    assert res.unread_count == 5

    # superadmin get_unread_count
    mock_db.execute.side_effect = [mock_pref_res, mock_count_res]
    res_sa = await notif_service.get_unread_count(mock_db, "t1", "user-1", ["superadmin"])
    assert res_sa.unread_count == 5

    # mark_notification_read when already read
    mock_notif = MagicMock()
    mock_notif.tenant_id = "t1"
    mock_notif.status = "read"
    nid = uuid4()
    mock_db.get.return_value = mock_notif

    res_read = await notif_service.mark_notification_read(mock_db, "t1", nid)
    assert res_read.marked_count == 1

    # mark_all_notifications_read with str role & superadmin
    mock_db.execute.side_effect = None
    mock_items_res = MagicMock()
    mock_items_res.scalars.return_value.all.return_value = [mock_notif]
    mock_db.execute.return_value = mock_items_res

    res_all = await notif_service.mark_all_notifications_read(mock_db, "t1", "user-1", "doctor")
    assert res_all.marked_count == 1

    res_all_sa = await notif_service.mark_all_notifications_read(mock_db, "t1", "user-1", ["super_admin"])
    assert res_all_sa.marked_count == 1

    # get_user_preferences when record exists
    mock_pref_rec = MagicMock()
    mock_pref_rec.user_id = "u-existing"
    mock_pref_rec.in_app_enabled = True
    mock_pref_rec.email_enabled = False
    mock_pref_rec.sms_enabled = True
    mock_pref_rec.categories_disabled = ["promo"]

    mock_p_res = MagicMock()
    mock_p_res.scalar_one_or_none.return_value = mock_pref_rec
    mock_db.execute.return_value = mock_p_res

    pref_res = await notif_service.get_user_preferences(mock_db, "u-existing")
    assert pref_res.sms_enabled is True

    # update_user_preferences existing record
    up_req = UpdatePreferenceRequest(in_app_enabled=False, email_enabled=False, sms_enabled=True, categories_disabled=["alert"])
    pref_up = await notif_service.update_user_preferences(mock_db, "u-existing", up_req)
    assert pref_up.user_id == "u-existing"

    # update_user_preferences with None fields
    up_req_none = UpdatePreferenceRequest(in_app_enabled=None, email_enabled=None, sms_enabled=None, categories_disabled=None)
    pref_up_none = await notif_service.update_user_preferences(mock_db, "u-existing", up_req_none)
    assert pref_up_none.user_id == "u-existing"

# ---------------------------------------------------------------------------
# Recipient Filter Unit Tests
# ---------------------------------------------------------------------------

class TestRecipientFilters:
    def test_build_recipient_filter_string_role(self):
        f = notif_srv._build_recipient_filter("user-123", "doctor")
        assert f is not None

    def test_build_recipient_filter_list_roles(self):
        f = notif_srv._build_recipient_filter("user-123", ["doctor", "nurse"])
        assert f is not None

    def test_build_recipient_filter_none_role(self):
        f = notif_srv._build_recipient_filter("user-123", None)
        assert f is not None


# ---------------------------------------------------------------------------
# Async Notification Operations Tests
# ---------------------------------------------------------------------------

class TestNotificationOperations:
    @pytest.mark.asyncio
    async def test_get_user_notifications_filters(self):
        mock_db = AsyncMock()
        mock_res1 = MagicMock()
        mock_res1.scalar.return_value = 5
        mock_res2 = MagicMock()
        mock_res2.scalar.return_value = 2
        mock_res3 = MagicMock()
        mock_res3.scalars.return_value.all.return_value = []
        mock_db.execute.side_effect = [mock_res1, mock_res2, mock_res3]

        res = await notif_srv.get_user_notifications(
            mock_db, tenant_id="t1", recipient_id="u1", recipient_role="superadmin",
            unread_only=True, category="clinical"
        )
        assert res.total == 5

    @pytest.mark.asyncio
    async def test_update_user_preferences_existing(self):
        mock_db = AsyncMock()
        mock_pref = MagicMock()
        mock_pref.user_id = "u1"
        mock_pref.in_app_enabled = True
        mock_pref.email_enabled = True
        mock_pref.sms_enabled = False
        mock_pref.categories_disabled = []
        mock_res = MagicMock()
        mock_res.scalar_one_or_none.return_value = mock_pref
        mock_db.execute.return_value = mock_res

        req = UpdatePreferenceRequest(in_app_enabled=False, email_enabled=False, sms_enabled=True, categories_disabled=["system"])
        res = await notif_srv.update_user_preferences(mock_db, "u1", req)
        assert res.user_id == "u1"

    @pytest.mark.asyncio
    async def test_get_user_notification_preferences_default(self):
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        pref = await notif_srv.get_user_preferences(mock_db, "u1")
        assert pref.email_enabled is True
        assert pref.in_app_enabled is True

    @pytest.mark.asyncio
    async def test_update_user_notification_preferences_existing(self):
        mock_db = AsyncMock()
        mock_existing = MagicMock()
        mock_existing.user_id = "u1"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_existing
        mock_db.execute.return_value = mock_result

        req = UpdatePreferenceRequest(email_enabled=False, in_app_enabled=True, categories_disabled=["billing"])
        pref = await notif_srv.update_user_preferences(mock_db, "u1", req)

        assert mock_existing.email_enabled is False
        assert mock_existing.categories_disabled == ["billing"]

    @pytest.mark.asyncio
    async def test_mark_notification_read_not_found(self):
        mock_db = AsyncMock()
        mock_db.get.return_value = None

        with pytest.raises(NotFoundError):
            await notif_srv.mark_notification_read(mock_db, "t1", uuid4())

    @pytest.mark.asyncio
    async def test_mark_notification_read_success(self):
        mock_db = AsyncMock()
        nid = uuid4()
        mock_notif = MagicMock()
        mock_notif.tenant_id = "t1"
        mock_notif.status = "unread"
        mock_db.get.return_value = mock_notif

        res = await notif_srv.mark_notification_read(mock_db, "t1", nid)
        assert res.marked_count == 1
        assert mock_notif.status == "read"

    @pytest.mark.asyncio
    async def test_get_unread_count(self):
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 5
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        res = await notif_srv.get_unread_count(mock_db, "t1", "u1", ["doctor"])
        assert res.unread_count == 5

    @pytest.mark.asyncio
    async def test_get_user_notifications(self):
        mock_db = AsyncMock()
        mock_notif = MagicMock()
        mock_notif.notification_id = uuid4()
        mock_notif.tenant_id = "t1"
        mock_notif.recipient_id = "u1"
        mock_notif.recipient_role = "doctor"
        mock_notif.title = "Alert"
        mock_notif.message = "Msg"
        mock_notif.category = "system"
        mock_notif.priority = "normal"
        mock_notif.status = "unread"
        mock_notif.action_url = None
        mock_notif.metadata_payload = None
        mock_notif.read_at = None
        mock_notif.created_at = datetime.now(timezone.utc)

        mock_res_count = MagicMock()
        mock_res_count.scalar.return_value = 1
        mock_res_items = MagicMock()
        mock_res_items.scalars.return_value.all.return_value = [mock_notif]

        mock_db.execute.side_effect = [mock_res_count, mock_res_count, mock_res_items]

        res = await notif_srv.get_user_notifications(
            mock_db, tenant_id="t1", recipient_id="u1", recipient_role="doctor", page=1, page_size=10
        )
        assert res.total == 1
        assert len(res.items) == 1

    @pytest.mark.asyncio
    async def test_mark_all_notifications_read(self):
        mock_db = AsyncMock()
        mock_notif = MagicMock()
        mock_notif.status = "unread"
        mock_res = MagicMock()
        mock_res.scalars.return_value.all.return_value = [mock_notif]
        mock_db.execute.return_value = mock_res

        res = await notif_srv.mark_all_notifications_read(mock_db, "t1", "u1", ["doctor"])
        assert res.marked_count == 1
        assert mock_notif.status == "read"

    @pytest.mark.asyncio
    async def test_create_notification(self):
        mock_db = AsyncMock()
        payload = NotificationCreateRequest(
            tenant_id="t1",
            recipient_id="u1",
            recipient_role="doctor",
            title="New Test",
            message="Test msg",
            category="system",
            priority="urgent",
        )

        res = await notif_srv.create_notification(mock_db, payload)
        assert res.title == "New Test"
        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()

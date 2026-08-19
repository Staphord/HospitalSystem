"""Unit test suite for notification-service business logic, filters, and notification preferences.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import notifications as notif_srv
from app.api.v1.schemas import (
    NotificationCreateRequest,
    UpdatePreferenceRequest,
)
from app.exceptions import NotFoundError


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
        mock_db.execute.return_value = mock_result

        res = await notif_srv.get_unread_count(mock_db, "t1", "u1", ["doctor"])
        assert res.unread_count == 5

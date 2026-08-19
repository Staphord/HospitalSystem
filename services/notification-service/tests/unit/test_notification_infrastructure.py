"""Infrastructure, SSE broadcaster, and security unit tests for notification-service.
"""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.services.broadcaster import NotificationBroadcaster
from app.exceptions import (
    UnauthorizedError,
    ForbiddenError,
    NotFoundError,
    ConflictError,
    BadRequestError,
    RateLimitError,
)


# ---------------------------------------------------------------------------
# Notification Broadcaster Unit Tests
# ---------------------------------------------------------------------------

class TestNotificationBroadcaster:
    @pytest.mark.asyncio
    async def test_subscribe_and_unsubscribe(self):
        broadcaster = NotificationBroadcaster()
        with patch("asyncio.create_task"):
            queue = broadcaster.subscribe("tenant-1")
            assert queue is not None
            assert "tenant-1" in broadcaster._local_queues

            broadcaster.unsubscribe("tenant-1", queue)
            assert "tenant-1" not in broadcaster._local_queues

    @pytest.mark.asyncio
    async def test_broadcast_publish_to_redis(self):
        broadcaster = NotificationBroadcaster()
        broadcaster._redis = AsyncMock()

        await broadcaster.broadcast("tenant-1", {"message": "alert"})
        broadcaster._redis.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connect_and_disconnect(self):
        broadcaster = NotificationBroadcaster()
        with patch("redis.asyncio.from_url", return_value=AsyncMock()) as mock_from_url:
            await broadcaster.connect()
            mock_from_url.assert_called_once()

            await broadcaster.disconnect()
            assert broadcaster._redis is None


# ---------------------------------------------------------------------------
# Custom Exception Hierarchies Tests
# ---------------------------------------------------------------------------

class TestNotificationCustomExceptions:
    def test_exception_instantiation(self):
        assert UnauthorizedError().status_code == 401
        assert ForbiddenError().status_code == 403
        assert NotFoundError().status_code == 404
        assert ConflictError().status_code == 409
        assert BadRequestError().status_code == 400
        assert RateLimitError().status_code == 429

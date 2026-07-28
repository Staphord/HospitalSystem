import asyncio
import json
import logging

import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger("service")

_CHANNEL_PREFIX = "notifications:"


class NotificationBroadcaster:
    """Manages real-time Server-Sent Event (SSE) delivery via Redis Pub/Sub."""

    def __init__(self) -> None:
        self._redis: aioredis.Redis | None = None
        self._local_queues: dict[str, set[asyncio.Queue]] = {}

    async def connect(self) -> None:
        """Establish async Redis connection."""
        self._redis = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        logger.info("NotificationBroadcaster connected to Redis")

    async def disconnect(self) -> None:
        """Close Redis connection and drain local queues."""
        if self._redis:
            await self._redis.aclose()
            self._redis = None
        self._local_queues.clear()
        logger.info("NotificationBroadcaster disconnected from Redis")

    def subscribe(self, tenant_id: str) -> asyncio.Queue:
        """Register a local SSE queue and start listening for this tenant channel."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        if tenant_id not in self._local_queues:
            self._local_queues[tenant_id] = set()
            asyncio.create_task(self._listen(tenant_id))
        self._local_queues[tenant_id].add(queue)
        return queue

    def unsubscribe(self, tenant_id: str, queue: asyncio.Queue) -> None:
        """Remove a subscriber queue when the SSE connection closes."""
        if tenant_id in self._local_queues:
            self._local_queues[tenant_id].discard(queue)
            if not self._local_queues[tenant_id]:
                del self._local_queues[tenant_id]

    async def broadcast(self, tenant_id: str, event_data: dict) -> None:
        """Publish a notification event to all replicas via Redis Pub/Sub."""
        if not self._redis:
            logger.warning("Broadcaster has no Redis connection; skipping publish")
            return
        channel = f"{_CHANNEL_PREFIX}{tenant_id}"
        payload = json.dumps(event_data, default=str)
        await self._redis.publish(channel, payload)

    async def _listen(self, tenant_id: str) -> None:
        """Subscribe to the Redis channel and fan out messages to local SSE queues."""
        if not self._redis:
            return
        channel = f"{_CHANNEL_PREFIX}{tenant_id}"
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                data: str = message["data"]
                queues = list(self._local_queues.get(tenant_id, []))
                if not queues:
                    break
                for queue in queues:
                    try:
                        queue.put_nowait(data)
                    except asyncio.QueueFull:
                        pass
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()


broadcaster = NotificationBroadcaster()

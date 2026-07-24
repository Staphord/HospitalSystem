import asyncio
import json
from typing import AsyncGenerator


class NotificationBroadcaster:
    """Manages real-time Server-Sent Event (SSE) subscriber queues per tenant."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = {}

    def subscribe(self, tenant_id: str) -> asyncio.Queue:
        """Subscribe to real-time events for a specific tenant."""
        queue: asyncio.Queue = asyncio.Queue()
        if tenant_id not in self._subscribers:
            self._subscribers[tenant_id] = set()
        self._subscribers[tenant_id].add(queue)
        return queue

    def unsubscribe(self, tenant_id: str, queue: asyncio.Queue) -> None:
        """Remove a subscriber queue when connection drops."""
        if tenant_id in self._subscribers:
            self._subscribers[tenant_id].discard(queue)
            if not self._subscribers[tenant_id]:
                del self._subscribers[tenant_id]

    async def broadcast(self, tenant_id: str, event_data: dict) -> None:
        """Publish event message to all connected subscriber queues for a tenant."""
        if tenant_id not in self._subscribers:
            return

        formatted = json.dumps(event_data, default=str)
        for queue in list(self._subscribers[tenant_id]):
            try:
                queue.put_nowait(formatted)
            except asyncio.QueueFull:
                pass


broadcaster = NotificationBroadcaster()

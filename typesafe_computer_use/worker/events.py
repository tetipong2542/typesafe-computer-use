"""Event broadcasting and SSE stream coordinator with Last-Event-ID support."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import defaultdict
from collections.abc import AsyncGenerator

from .db import WorkerDatabase
from .state import TaskEvent


class EventHub:
    """Pub/Sub event broadcaster for realtime SSE streams."""

    def __init__(self, db: WorkerDatabase):
        self.db = db
        self._subscribers: dict[str, set[asyncio.Queue[TaskEvent]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def publish(self, event: TaskEvent) -> None:
        """Persist event to database and broadcast to all active subscribers."""
        # Persist synchronously in DB
        self.db.save_event(event)

        # Broadcast to active queues
        async with self._lock:
            queues = list(self._subscribers.get(event.task_id, set()))

        for q in queues:
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)

    async def subscribe(
        self,
        task_id: str,
        last_event_id: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Stream events for a task, replaying past events since last_event_id if requested."""
        queue: asyncio.Queue[TaskEvent] = asyncio.Queue(maxsize=100)

        async with self._lock:
            self._subscribers[task_id].add(queue)

        try:
            # 1. Replay historical events if last_event_id provided or initial connect
            historical = self.db.get_events(task_id, last_event_id=last_event_id)
            for event in historical:
                yield {
                    "id": event.event_id,
                    "event": event.phase.value,
                    "data": json.dumps(event.to_dict()),
                }

            # 2. Stream live events
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield {
                        "id": event.event_id,
                        "event": event.phase.value,
                        "data": json.dumps(event.to_dict()),
                    }
                except TimeoutError:
                    # Send keep-alive comment
                    yield {"comment": "keep-alive"}
        finally:
            async with self._lock:
                self._subscribers[task_id].discard(queue)
                if not self._subscribers[task_id]:
                    self._subscribers.pop(task_id, None)

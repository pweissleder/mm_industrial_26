"""Live presence: pushes a building's people to its WebSocket subscribers when they change.

`PresenceService` reports which buildings changed (`notify`, callable from any thread); each
subscriber then receives the building's current people once. Notifications are coalesced per
subscriber (at most one pending), so a burst of movement costs one snapshot, never a queue that
grows. Lease expiry is not an event of its own: `watch` polls occupancy and reports the buildings
of people who went offline or came online.
"""

import asyncio
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager

from app.presence.service import PresenceService

EXPIRY_CHECK_SECONDS = 5.0


class LiveHub:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[str]]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def notify(self, building_ids: Iterable[str | None]) -> None:
        """Thread-safe: these buildings' people changed."""
        ids = {b for b in building_ids if b}
        loop = self._loop
        if ids and loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._fan_out, ids)

    def _fan_out(self, building_ids: set[str]) -> None:
        with self._lock:
            queues = [q for b in building_ids for q in self._subscribers.get(b, ())]
        for queue in queues:
            if queue.empty():  # one pending wake-up is enough: the snapshot is always current
                queue.put_nowait("changed")

    @contextmanager
    def subscribe(self, building_id: str) -> Iterator[asyncio.Queue[str]]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        with self._lock:
            self._subscribers.setdefault(building_id, set()).add(queue)
        try:
            yield queue
        finally:
            with self._lock:
                subscribers = self._subscribers.get(building_id, set())
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(building_id, None)

    async def watch(self, presence: PresenceService, every: float = EXPIRY_CHECK_SECONDS) -> None:
        """Report people whose lease ran out (offline) or who came online meanwhile."""
        before = presence.leases.occupied()
        while True:
            await asyncio.sleep(every)
            now = presence.leases.occupied()
            if now != before:
                self.notify(presence.building_of(p) for p in now ^ before)
                before = now

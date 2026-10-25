"""Global agent memory: chat messages of every conversation plus raw navigation events.

Process-local and bounded (lost on restart). Events stay raw (searches by category, planned
routes with signed floor changes) so later statistics can slice them any way; `stats()` is the
running summary the assistant and the API show.
"""

import copy
import json
import secrets
from collections import OrderedDict, deque
from datetime import UTC, datetime
from threading import Lock

from app.assistant.port import JSON

MAX_MESSAGES = 2000
MAX_EVENTS = 5000
MAX_RECAPS = 50
VERTICAL = ("stairs", "lift")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def category_key(category: str) -> str:
    """`room_type:toilets` -> `toilet`, `asset_type:coffee_machine` -> `coffee_machine`,
    free text `Coffee machines` -> `coffee_machine` (mirrors NavigationService matching)."""
    value = category.strip().lower()
    if ":" in value:
        value = value.split(":", 1)[1]
    value = "_".join(value.replace("-", " ").split())
    if len(value) > 3 and value.endswith("s") and not value.endswith("ss"):
        value = value[:-1]
    return value


class AgentMemory:
    def __init__(self, max_messages: int = MAX_MESSAGES, max_events: int = MAX_EVENTS) -> None:
        self.lock = Lock()
        self.messages: deque[JSON] = deque(maxlen=max_messages)
        self.events: deque[JSON] = deque(maxlen=max_events)
        # Conversation lifecycle for recaps: open -> ended (reset, or a newer one started).
        self.open: dict[str, str] = {}
        self.ended: dict[str, str] = {}
        self.recapped: set[str] = set()
        self.recaps: OrderedDict[str, JSON] = OrderedDict()

    # ---------- recording ----------
    def add_message(
        self,
        session_id: str,
        building_id: str | None,
        role: str,
        text: str,
        voice: bool = False,
    ) -> None:
        at = now()
        with self.lock:
            if role == "user" and session_id not in self.open and session_id not in self.ended:
                for other in list(self.open):  # a new conversation ends the earlier ones
                    self.ended[other] = self.open.pop(other)
            if session_id not in self.ended:
                self.open[session_id] = at
            self.messages.append(
                {
                    "at": at,
                    "session_id": session_id,
                    "building_id": building_id,
                    "role": role,
                    "text": text,
                    "voice": voice,
                }
            )

    def end_session(self, session_id: str) -> None:
        with self.lock:
            if session_id in self.open:
                self.ended[session_id] = self.open.pop(session_id)

    def add_search(
        self,
        session_id: str,
        building_id: str | None,
        tool: str,
        raw: str,
        result_count: int,
    ) -> None:
        self._add(
            {
                "kind": "search",
                "session_id": session_id,
                "building_id": building_id,
                "tool": tool,
                "category": category_key(raw),
                "raw": raw,
                "results": result_count,
            }
        )

    def add_route(self, session_id: str, building_id: str | None, route: JSON) -> bool:
        """Record a planned route; the same start, target and mode planned again in the same
        session right after is not counted twice. Returns whether it was recorded."""
        key = _route_key(route)
        with self.lock:
            for event in reversed(self.events):
                if event["kind"] == "route" and event["session_id"] == session_id:
                    if _route_key(event) == key:
                        return False
                    break
        self._add({"kind": "route", "session_id": session_id, "building_id": building_id} | route)
        return True

    def _add(self, event: JSON) -> None:
        with self.lock:
            self.events.append({"at": now()} | event)

    def clear(self) -> None:
        with self.lock:
            self.messages.clear()
            self.events.clear()
            self.open.clear()
            self.ended.clear()
            self.recapped.clear()
            self.recaps.clear()

    # ---------- recaps ----------
    def due_sessions(self, count: int) -> list[str]:
        """The `count` earliest ended conversations not in a recap yet, once there are that many."""
        with self.lock:
            waiting = [s for s in self.ended if s not in self.recapped]
        return waiting[:count] if count > 0 and len(waiting) >= count else []

    def last_sessions(self, count: int) -> list[str]:
        """The latest `count` conversations with at least one message (ended or not)."""
        with self.lock:
            seen = list(dict.fromkeys(m["session_id"] for m in self.messages))
        return seen[-count:] if count > 0 else []

    def claim_recap(self, session_ids: list[str]) -> JSON:
        record: JSON = {
            "id": secrets.token_urlsafe(9),
            "status": "pending",
            "created_at": now(),
            "session_ids": list(session_ids),
        }
        with self.lock:
            self.recapped.update(session_ids)
            self.recaps[record["id"]] = record
            while len(self.recaps) > MAX_RECAPS:
                self.recaps.popitem(last=False)
        return copy.deepcopy(record)

    def update_recap(self, recap_id: str, values: JSON) -> None:
        with self.lock:
            if recap_id in self.recaps:
                self.recaps[recap_id] |= values

    def recap(self, recap_id: str | None = None) -> JSON | None:
        """One recap, or the latest when no id is given."""
        with self.lock:
            if recap_id is None:
                found = next(reversed(self.recaps.values()), None)
            else:
                found = self.recaps.get(recap_id)
            return copy.deepcopy(found)

    def conversation(self, session_ids: list[str]) -> tuple[list[JSON], list[JSON]]:
        """Messages and events of the given conversations, oldest first."""
        wanted = set(session_ids)
        with self.lock:
            messages = [m for m in self.messages if m["session_id"] in wanted]
            events = [e for e in self.events if e["session_id"] in wanted]
        return copy.deepcopy(messages), copy.deepcopy(events)

    # ---------- reading ----------
    def recent_messages(self, limit: int = 20) -> list[JSON]:
        with self.lock:
            return copy.deepcopy(list(self.messages)[-limit:])

    def search_messages(self, query: str, limit: int = 20) -> list[JSON]:
        needle = query.strip().lower()
        with self.lock:
            found = [m for m in self.messages if needle and needle in m["text"].lower()]
        return copy.deepcopy(found[-limit:])

    def recent_events(self, kind: str | None = None, limit: int = 100) -> list[JSON]:
        with self.lock:
            rows = [e for e in self.events if kind is None or e["kind"] == kind]
        return copy.deepcopy(rows[-limit:])

    def stats(self) -> JSON:
        with self.lock:
            messages, events = list(self.messages), list(self.events)
        return aggregate(messages, events)


def aggregate(messages: list[JSON], events: list[JSON]) -> JSON:
    """Summary of any slice of the memory (everything, or the conversations of one recap)."""
    by_category: dict[str, int] = {}
    routes: JSON = {
        "count": 0,
        "distance_m": 0.0,
        "official_cost_m": 0.0,
        "floor_changes": dict.fromkeys(VERTICAL, 0),
        "climbed_m": dict.fromkeys(VERTICAL, 0.0),
        "descended_m": dict.fromkeys(VERTICAL, 0.0),
    }
    by_building: dict[str, JSON] = {}
    for event in events:
        if event["kind"] == "search":
            by_category[event["category"]] = by_category.get(event["category"], 0) + 1
        elif event["kind"] == "route" and event.get("reachable") is not False:
            distance = event.get("walk_m")
            distance = event.get("length_m") if distance is None else distance
            routes["count"] += 1
            routes["distance_m"] += distance or 0.0
            routes["official_cost_m"] += event.get("length_m") or 0.0
            for change in event.get("floor_changes", []):
                kind, delta = change.get("kind"), change.get("delta_m") or 0.0
                if kind not in VERTICAL:
                    continue
                routes["floor_changes"][kind] += 1
                routes["climbed_m" if delta > 0 else "descended_m"][kind] += abs(delta)
            building = by_building.setdefault(
                str(event.get("building_id")), {"routes": 0, "distance_m": 0.0}
            )
            building["routes"] += 1
            building["distance_m"] += distance or 0.0
    for key in ("distance_m", "official_cost_m"):
        routes[key] = round(routes[key], 1)
    for key in ("climbed_m", "descended_m"):
        routes[key] = {k: round(v, 2) for k, v in routes[key].items()}
    for building in by_building.values():
        building["distance_m"] = round(building["distance_m"], 1)
    stamps = [m["at"] for m in messages] + [e["at"] for e in events]
    sessions = {m["session_id"] for m in messages} | {e["session_id"] for e in events}
    return {
        "since": min(stamps) if stamps else None,
        "turns": sum(1 for m in messages if m["role"] == "user"),
        "sessions": len(sessions),
        "searches": {
            "total": sum(by_category.values()),
            "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
        },
        "routes": routes,
        "by_building": by_building,
    }


def _route_key(route: JSON) -> str:
    return json.dumps(
        [route.get("start"), route.get("target"), route.get("mode")], sort_keys=True, default=str
    )

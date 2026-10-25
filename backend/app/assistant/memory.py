"""Global agent memory: chat messages of every conversation plus raw navigation events.

Process-local and bounded (lost on restart). Events stay raw (searches by category, planned
routes with signed floor changes) so later statistics can slice them any way; `stats()` is the
running summary the assistant and the API show.
"""

import copy
import json
from collections import deque
from datetime import UTC, datetime
from threading import Lock

from app.assistant.port import JSON

MAX_MESSAGES = 2000
MAX_EVENTS = 5000
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

    # ---------- recording ----------
    def add_message(self, session_id: str, building_id: str | None, role: str, text: str) -> None:
        with self.lock:
            self.messages.append(
                {
                    "at": now(),
                    "session_id": session_id,
                    "building_id": building_id,
                    "role": role,
                    "text": text,
                }
            )

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

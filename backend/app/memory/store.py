"""Opt-in local activity memory: one JSONL file per pseudonymous session, plus a recap.

The first line of a session file is its header (kind, creation time); every further line is one
event with a server timestamp. Nothing is written for a session the client did not create, and a
deleted session cannot be resurrected: events are only appended to an existing file, and ids are
random. The recap is computed from the events alone (no model, no invented numbers).
"""

from __future__ import annotations

import json
import re
import secrets
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

SessionKind = Literal["real", "demo", "test"]
EventType = Literal[
    "search_intent",
    "destination_confirmed",
    "nav_start",
    "nav_pause",
    "nav_resume",
    "nav_stop",
    "moved",
    "reroute",
    "arrival",
    "setting_confirmed",
]

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

LABELS: dict[str, str] = {
    "real": "Your session",
    "demo": "Demo session (synthetic)",
    "test": "Test session (not user history)",
}


class SessionNotFoundError(LookupError):
    """The session was never created, was deleted, or the id is malformed."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    kind: SessionKind
    created_at: str


class MemoryStore:
    """JSONL files under `root`; thread-safe for the single-process API."""

    def __init__(self, root: Path, clock: Callable[[], datetime] = _utcnow) -> None:
        self.root = root
        self._clock = clock
        self._lock = threading.Lock()

    def _path(self, session_id: str) -> Path:
        if not SESSION_ID_RE.fullmatch(session_id):
            raise SessionNotFoundError(session_id)
        return self.root / f"{session_id}.jsonl"

    def _now(self) -> str:
        return self._clock().astimezone(UTC).isoformat()

    def create_session(self, kind: SessionKind) -> SessionInfo:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock:
            while True:
                session_id = secrets.token_urlsafe(16)
                path = self._path(session_id)
                if not path.exists():
                    break
            info = SessionInfo(session_id=session_id, kind=kind, created_at=self._now())
            header = {"type": "session", "kind": kind, "created_at": info.created_at}
            with path.open("x", encoding="utf-8") as f:
                f.write(json.dumps(header) + "\n")
        return info

    def append(
        self, session_id: str, event_type: EventType, data: dict[str, Any]
    ) -> dict[str, Any]:
        path = self._path(session_id)
        with self._lock:
            if not path.is_file():
                raise SessionNotFoundError(session_id)
            event = {"type": event_type, "at": self._now(), "data": data}
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def read(self, session_id: str) -> tuple[SessionInfo, list[dict[str, Any]]]:
        path = self._path(session_id)
        with self._lock:
            if not path.is_file():
                raise SessionNotFoundError(session_id)
            lines = path.read_text(encoding="utf-8").splitlines()
        header = json.loads(lines[0])
        info = SessionInfo(session_id, header["kind"], header["created_at"])
        return info, [json.loads(line) for line in lines[1:] if line.strip()]

    def delete(self, session_id: str) -> None:
        path = self._path(session_id)
        with self._lock:
            if not path.is_file():
                raise SessionNotFoundError(session_id)
            path.unlink()


def _ts(event: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(str(event["at"]))


def compute_recap(info: SessionInfo, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic recap. Only events inside a navigation (nav_start .. nav_stop/arrival) count
    as movement or arrival; one arrival per navigation; paused time is excluded."""
    arrivals: list[str] = []
    floors: list[str] = []
    walked = 0.0
    started = completed = reroutes = 0
    seconds = 0.0

    open_nav = False
    nav_start: datetime | None = None
    paused_at: datetime | None = None
    paused_s = 0.0
    last_at: datetime | None = None

    def add_floor(name: object) -> None:
        if isinstance(name, str) and name and name not in floors:
            floors.append(name)

    def close(at: datetime) -> None:
        nonlocal open_nav, seconds, nav_start, paused_at, paused_s
        if nav_start is not None:
            pause = paused_s + ((at - paused_at).total_seconds() if paused_at else 0.0)
            seconds += max(0.0, (at - nav_start).total_seconds() - pause)
        open_nav, nav_start, paused_at, paused_s = False, None, None, 0.0

    for event in events:
        kind = event.get("type")
        data = event.get("data") or {}
        at = _ts(event)
        last_at = at
        if kind == "nav_start":
            if open_nav:
                close(at)
            open_nav, nav_start, started = True, at, started + 1
        elif not open_nav:
            continue  # movement/arrivals outside a navigation never count
        elif kind == "nav_pause" and paused_at is None:
            paused_at = at
        elif kind == "nav_resume" and paused_at is not None:
            paused_s += (at - paused_at).total_seconds()
            paused_at = None
        elif kind == "moved":
            walked += float(data.get("xy_m", 0.0))
            add_floor(data.get("floor_name"))
        elif kind == "reroute":
            reroutes += 1
        elif kind == "arrival":
            arrivals.append(str(data.get("name", "")))
            add_floor(data.get("floor_name"))
            completed += 1
            close(at)
        elif kind == "nav_stop":
            close(at)
    if open_nav and last_at is not None:
        close(last_at)

    counts = Counter(arrivals)
    places = list(dict.fromkeys(arrivals))
    favourite = max(places, key=lambda p: (counts[p], -places.index(p))) if places else None
    return {
        "session_id": info.session_id,
        "kind": info.kind,
        "label": LABELS[info.kind],
        "synthetic": info.kind != "real",
        "created_at": info.created_at,
        "event_count": len(events),
        "places_reached": places,
        "places_reached_count": len(places),
        "arrivals": len(arrivals),
        "walked_m": round(walked, 2),
        "floors_visited": floors,
        "favourite_destination": favourite,
        "navigations_started": started,
        "navigations_completed": completed,
        "reroutes": reroutes,
        "simulated_minutes": round(seconds / 60.0, 2),
    }

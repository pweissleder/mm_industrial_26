"""Where each person is, in a small SQLite file (standard library `sqlite3`).

A row is a fact: "this person is in <building> (their current location), last at <endpoint> there,
as of <updated_at>". A person is in at most one building; the map position is optional (someone
who opened a building but set no place is there without a spot). Rows survive disconnects and
backend restarts. Clearing the position keeps the building; `revision` only ever increases per
person, so an older write can never win.
"""

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

JSON = dict[str, Any]

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    profile_id TEXT PRIMARY KEY,
    building_id TEXT,
    position TEXT,
    revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class StoredPosition:
    profile_id: str
    building_id: str
    position: JSON | None
    revision: int
    updated_at: str


class PositionStore:
    def __init__(self, path: Path | str) -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.execute(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _row(row: tuple[Any, ...] | None) -> StoredPosition | None:
        if row is None or row[1] is None:
            return None
        return StoredPosition(
            row[0], row[1], json.loads(row[2]) if row[2] else None, row[3], row[4]
        )

    def get(self, profile_id: str) -> StoredPosition | None:
        with self._lock:
            row = self._db.execute(
                "SELECT profile_id, building_id, position, revision, updated_at"
                " FROM positions WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()
        return self._row(row)

    def has_record(self, profile_id: str) -> bool:
        """Whether the person ever had a position (a cleared one still counts)."""
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM positions WHERE profile_id = ?", (profile_id,)
            ).fetchone()
        return row is not None

    def in_building(self, building_id: str) -> list[StoredPosition]:
        with self._lock:
            rows = self._db.execute(
                "SELECT profile_id, building_id, position, revision, updated_at"
                " FROM positions WHERE building_id = ? ORDER BY profile_id",
                (building_id,),
            ).fetchall()
        return [p for p in map(self._row, rows) if p is not None]

    def _write(
        self, profile_id: str, building_id: str | None, position: JSON | None, *, keep: bool = False
    ) -> tuple[int, str, str | None]:
        """Write a row; with `keep`, the stored building stays (only the position changes)."""
        now = datetime.now(UTC).isoformat()
        payload = None if position is None else json.dumps(position, separators=(",", ":"))
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT revision, building_id FROM positions WHERE profile_id = ?", (profile_id,)
            ).fetchone()
            revision = (row[0] if row else 0) + 1
            if keep:
                building_id = row[1] if row else None
            self._db.execute(
                "INSERT INTO positions (profile_id, building_id, position, revision, updated_at)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT(profile_id) DO UPDATE SET"
                " building_id = excluded.building_id, position = excluded.position,"
                " revision = excluded.revision, updated_at = excluded.updated_at",
                (profile_id, building_id, payload, revision, now),
            )
        return revision, now, building_id

    def put(self, profile_id: str, building_id: str, position: JSON | None) -> StoredPosition:
        """The person is now in `building_id`, at `position` (None: somewhere unknown there)."""
        revision, now, _ = self._write(profile_id, building_id, position)
        return StoredPosition(profile_id, building_id, position, revision, now)

    def clear(self, profile_id: str) -> StoredPosition | None:
        """Remove the map position; the person stays in their building."""
        revision, now, building_id = self._write(profile_id, None, None, keep=True)
        return (
            None
            if building_id is None
            else StoredPosition(profile_id, building_id, None, revision, now)
        )

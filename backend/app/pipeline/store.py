"""JSON workspace: locations, runs, the asset library and cross-run memory.

Layout under `workspace_dir`:

    locations/<location_id>/raw.json, building.json, patches.json, issues.json,
                            location.json, model.glb, model_index.json, previews/, reports/
    runs/<run_id>.json
    asset_library/<type>.json     (falls back to the committed seed library)
    memory/source_quirks.json

Writes are atomic (temp file + rename), so readers never see half-written JSON.
"""

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field

from app.pipeline.state import Issue, Patch, RunRecord, utcnow
from app.pipeline.tools.geo3d.spec import LibraryEntry

M = TypeVar("M", bound=BaseModel)
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_json(path: Path, data: Any) -> None:
    _write_atomic(path, json.dumps(data, indent=2, ensure_ascii=False, default=str))


def write_model(path: Path, model: BaseModel) -> None:
    _write_atomic(path, model.model_dump_json(indent=2))


def read_model(path: Path, cls: type[M]) -> M | None:
    return cls.model_validate_json(path.read_bytes()) if path.is_file() else None


def _check_id(value: str) -> str:
    if not SAFE_ID.match(value):
        raise ValueError(f"unsafe id: {value!r}")
    return value


# --- location record -----------------------------------------------------------------

PillarState = Literal["pending", "running", "done", "degraded", "failed", "skipped"]


class Pillar(BaseModel):
    status: PillarState = "pending"
    updated_at: datetime | None = None
    note: str | None = None


class SpaceMeta(BaseModel):
    space_id: str
    floor_id: str
    name: str
    ref: str
    type: str
    room_type: str
    area_m2: float
    description: str = ""
    equipment: dict[str, int] = Field(default_factory=dict, description="type -> count")
    doors: int = 0
    step_free_access: bool = True
    issues: list[str] = Field(default_factory=list)


class FloorMeta(BaseModel):
    floor_id: str
    level: int
    name: str
    description: str = ""
    stats: dict[str, Any] = Field(default_factory=dict)


class LocationMetadata(BaseModel):
    summary: str = ""
    key_facts: list[str] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    floors: list[FloorMeta] = Field(default_factory=list)
    spaces: dict[str, SpaceMeta] = Field(default_factory=dict)


class LocationRecord(BaseModel):
    id: str
    dataset: str
    name: str
    source: str
    raw_sha256: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    pillars: dict[str, Pillar] = Field(
        default_factory=lambda: {
            p: Pillar() for p in ("validation", "graph", "assets_3d", "metadata")
        }
    )
    metadata: LocationMetadata | None = None
    artifacts: dict[str, str] = Field(default_factory=dict, description="name -> relative path")
    runs: list[str] = Field(default_factory=list)


class LocationStore:
    def __init__(self, root: Path) -> None:
        self.root = root / "locations"

    def dir(self, location_id: str) -> Path:
        return self.root / _check_id(location_id)

    def path(self, location_id: str, name: str) -> Path:
        return self.dir(location_id) / name

    def get(self, location_id: str) -> LocationRecord | None:
        return read_model(self.path(location_id, "location.json"), LocationRecord)

    def save(self, record: LocationRecord) -> None:
        record.updated_at = utcnow()
        write_model(self.path(record.id, "location.json"), record)

    def all(self) -> list[LocationRecord]:
        if not self.root.is_dir():
            return []
        records = (read_model(p, LocationRecord) for p in self.root.glob("*/location.json"))
        return sorted((r for r in records if r), key=lambda r: r.name)

    def set_pillar(
        self, location_id: str, pillar: str, status: PillarState, note: str | None = None
    ) -> None:
        record = self.get(location_id)
        if record is None:
            return
        record.pillars[pillar] = Pillar(status=status, updated_at=utcnow(), note=note)
        self.save(record)

    # Cleaned building (the working copy every stage reads) and its history.
    def read_building(self, location_id: str, raw: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(
            self.path(location_id, "raw.json" if raw else "building.json").read_bytes()
        )
        return data

    def write_building(self, location_id: str, data: dict[str, Any]) -> None:
        write_json(self.path(location_id, "building.json"), data)

    def read_issues(self, location_id: str) -> list[Issue]:
        path = self.path(location_id, "issues.json")
        if not path.is_file():
            return []
        return [Issue.model_validate(i) for i in json.loads(path.read_bytes())]

    def write_issues(self, location_id: str, issues: list[Issue]) -> None:
        write_json(
            self.path(location_id, "issues.json"), [i.model_dump(mode="json") for i in issues]
        )

    def read_patches(self, location_id: str) -> list[Patch]:
        path = self.path(location_id, "patches.json")
        if not path.is_file():
            return []
        return [Patch.model_validate(p) for p in json.loads(path.read_bytes())]

    def append_patch(self, location_id: str, patch: Patch) -> None:
        patches = self.read_patches(location_id)
        patches.append(patch)
        write_json(
            self.path(location_id, "patches.json"), [p.model_dump(mode="json") for p in patches]
        )


class RunStore:
    def __init__(self, root: Path) -> None:
        self.root = root / "runs"
        self._lock = Lock()

    def get(self, run_id: str) -> RunRecord | None:
        return read_model(self.root / f"{_check_id(run_id)}.json", RunRecord)

    def save(self, record: RunRecord) -> None:
        record.updated_at = utcnow()
        with self._lock:
            write_model(self.root / f"{_check_id(record.run_id)}.json", record)

    def all(self) -> list[RunRecord]:
        if not self.root.is_dir():
            return []
        records = (read_model(p, RunRecord) for p in self.root.glob("*.json"))
        return sorted((r for r in records if r), key=lambda r: r.created_at, reverse=True)


class AssetLibrary:
    """Type -> spec. Workspace entries override the committed seed library."""

    def __init__(self, root: Path, seed_dir: Path) -> None:
        self.root = root / "asset_library"
        self.seed_dir = seed_dir

    def get(self, asset_type: str) -> LibraryEntry | None:
        if not SAFE_ID.match(asset_type):
            return None  # can't be a file name, so it has no entry
        name = f"{asset_type}.json"
        return read_model(self.root / name, LibraryEntry) or read_model(
            self.seed_dir / name, LibraryEntry
        )

    def types(self) -> set[str]:
        found = {p.stem for p in self.seed_dir.glob("*.json")} if self.seed_dir.is_dir() else set()
        if self.root.is_dir():
            found |= {p.stem for p in self.root.glob("*.json")}
        return found

    def save(self, entry: LibraryEntry) -> LibraryEntry:
        previous = self.get(entry.spec.type)
        if previous is not None and previous.provenance != "fallback":
            entry = entry.model_copy(update={"version": previous.version + 1})
        write_model(self.root / f"{_check_id(entry.spec.type)}.json", entry)
        return entry


class MemoryStore:
    """Cross-run lessons: per-source data quirks and the recipes that fixed them."""

    def __init__(self, root: Path) -> None:
        self.path = root / "memory" / "source_quirks.json"
        self._lock = Lock()

    def quirks(self, source: str) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        data: dict[str, list[dict[str, Any]]] = json.loads(self.path.read_bytes())
        return data.get(source, [])

    def record(self, source: str, issue_class: str, lesson: str, run_id: str | None) -> None:
        with self._lock:
            data: dict[str, list[dict[str, Any]]] = (
                json.loads(self.path.read_bytes()) if self.path.is_file() else {}
            )
            entries = data.setdefault(source, [])
            entries[:] = [e for e in entries if e.get("issue_class") != issue_class]
            entries.append(
                {"issue_class": issue_class, "lesson": lesson, "run_id": run_id, "at": utcnow()}
            )
            write_json(self.path, data)


class Workspace:
    def __init__(self, root: Path, seed_dir: Path) -> None:
        self.root = root
        self.locations = LocationStore(root)
        self.runs = RunStore(root)
        self.library = AssetLibrary(root, seed_dir)
        self.memory = MemoryStore(root)

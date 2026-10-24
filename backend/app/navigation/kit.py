"""Read-only access to the official kit 1.1.0 Train/Dev material for the benchmark harness and the
Train/Dev example endpoint (ported from the prototype's `data.py`).

Nothing here is needed for routing: buildings for the app come from the BuildingRepository.
Sources, both optional and outside the repo:
- `SYNERA_REFERENCE_DIR`: a git checkout of the starter kit containing commit KIT_COMMIT (questions,
  verified per git blob id against that commit via `git ls-tree`, no network);
- `SYNERA_KIT_ZIP`: the official kit ZIP (buildings, unchanged in 1.1.0; each building.json is
  checked against the commit's blob id).
"""

import json
import os
import subprocess
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from app.navigation.errors import NavigationError
from app.navigation.official import KIT_COMMIT, KIT_VERSION, PINNED_BLOBS, git_blob_id

MAX_MEMBER = 20_000_000
SPLITS = ("train", "dev")


def reference_dir() -> Path:
    default = Path.home() / "Documents" / "HackathonTransfer" / "SyneraStarterReference"
    return Path(os.environ.get("SYNERA_REFERENCE_DIR") or default)


def kit_zip() -> Path:
    default = Path.home() / "Downloads" / "industrial-agents-hackathon-2026-main.zip"
    return Path(os.environ.get("SYNERA_KIT_ZIP") or default)


def questions_available() -> tuple[bool, str]:
    """Whether the kit 1.1.0 questions can be read, with a reason when not."""
    try:
        pinned_tree(str(reference_dir()))
    except NavigationError as error:
        return False, error.message
    return True, f"kit {KIT_VERSION} questions from {reference_dir()}"


def buildings_available() -> tuple[bool, str]:
    if not kit_zip().is_file():
        return False, f"Kit ZIP not found: {kit_zip()} (set SYNERA_KIT_ZIP)"
    return True, f"kit buildings from {kit_zip()}"


@lru_cache(maxsize=2)
def pinned_tree(reference: str) -> dict[str, str]:
    """{path: git blob id} of KIT_COMMIT, read from the reference checkout's object store."""
    env = {**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"}
    if not Path(reference).is_dir():
        raise NavigationError(
            "not_configured",
            f"Kit reference checkout not found: {reference} (set SYNERA_REFERENCE_DIR)",
        )
    try:
        out = subprocess.run(
            ["git", "-C", reference, "ls-tree", "-r", "--full-tree", KIT_COMMIT],
            capture_output=True,
            check=True,
            env=env,
            timeout=60,
        ).stdout.decode("utf-8")
    except (OSError, subprocess.SubprocessError) as error:
        raise NavigationError(
            "not_configured",
            f"Kit {KIT_VERSION} commit {KIT_COMMIT[:7]} not readable in {reference}",
        ) from error
    tree = {}
    for line in out.splitlines():
        meta, path = line.split("\t", 1)
        tree[path] = meta.split()[2]
    for path, blob in PINNED_BLOBS.values():
        if tree.get(path) != blob:
            raise NavigationError(
                "source_drift", "Pinned module blob does not match the kit commit", {"path": path}
            )
    return tree


@lru_cache(maxsize=2)
def _zip_members(archive: str) -> tuple[str, frozenset[str]]:
    with ZipFile(archive) as source:
        prefix = source.namelist()[0].split("/")[0] + "/"
        return prefix, frozenset(source.namelist())


def read_member(relative: str) -> bytes:
    archive = kit_zip()
    if not archive.is_file():
        raise NavigationError(
            "not_configured", f"Kit ZIP not found: {archive} (set SYNERA_KIT_ZIP)"
        )
    prefix, names = _zip_members(str(archive))
    if ".." in relative.split("/") or relative.startswith("/") or prefix + relative not in names:
        raise NavigationError("invalid_input", "Unknown official ZIP member", {"path": relative})
    with ZipFile(archive) as source:
        info = source.getinfo(prefix + relative)
        if info.file_size > MAX_MEMBER:
            raise NavigationError("computation_limit", "ZIP member too large")
        return source.read(info)


def kit_file(relative: str) -> bytes:
    """Bytes of `relative` exactly as in KIT_COMMIT (checkout file or identical ZIP member)."""
    expected = pinned_tree(str(reference_dir())).get(relative)
    if expected is None:
        raise NavigationError(
            "invalid_input", "File is not part of the pinned kit commit", {"path": relative}
        )
    path = reference_dir() / relative
    if path.is_file():
        content = path.read_bytes()
        if git_blob_id(content) != expected:
            raise NavigationError(
                "source_drift",
                "Reference file differs from the pinned kit commit",
                {"path": relative},
            )
        return content
    try:
        content = read_member(relative)
    except NavigationError:
        content = None
    if content is not None and git_blob_id(content, normalise_crlf=False) == expected:
        return content
    raise NavigationError(
        "source_unavailable",
        f"{relative} of kit {KIT_VERSION} is neither checked out nor identical in the ZIP",
        {"path": relative},
    )


def building_index() -> dict[str, str]:
    """{building_id: 'data/<split>/<id>/building.json'} for the Train/Dev buildings in the ZIP."""
    archive = kit_zip()
    if not archive.is_file():
        raise NavigationError(
            "not_configured", f"Kit ZIP not found: {archive} (set SYNERA_KIT_ZIP)"
        )
    prefix, names = _zip_members(str(archive))
    result = {}
    for name in sorted(names):
        rel = name.removeprefix(prefix).split("/")
        if len(rel) == 4 and rel[0] == "data" and rel[1] in SPLITS and rel[3] == "building.json":
            result[rel[2]] = "/".join(rel)
    return result


def load_building_bytes(building_id: str) -> bytes:
    """building.json of a Train/Dev building from the ZIP, verified against the kit 1.1.0 blob."""
    index = building_index()
    if building_id not in index:
        raise NavigationError(
            "invalid_input", "Not a Train/Dev building of the kit", {"id": building_id}
        )
    raw = read_member(index[building_id])
    if git_blob_id(raw, normalise_crlf=False) != pinned_tree(str(reference_dir())).get(
        index[building_id]
    ):
        raise NavigationError(
            "source_drift",
            "building.json in the ZIP differs from the pinned kit commit",
            {"id": building_id},
        )
    return raw


def _question_files(split: str, filename: str) -> Iterator[str]:
    if split not in SPLITS:
        raise NavigationError("invalid_input", "Train/Dev only")
    tree = pinned_tree(str(reference_dir()))
    for path in sorted(tree):
        parts = path.split("/")
        if len(parts) == 4 and parts[0] == "data" and parts[1] == split and parts[3] == filename:
            yield path


def _questions(split: str, filename: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in _question_files(split, filename):
        for line in kit_file(path).decode("utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


@lru_cache(maxsize=4)
def examples(split: str = "dev") -> tuple[dict[str, Any], ...]:
    """Kit 1.1.0 benchmark questions with answers (questions.jsonl). Train/Dev only, never Test."""
    return tuple(_questions(split, "questions.jsonl"))


@lru_cache(maxsize=4)
def basic_examples(split: str = "dev") -> tuple[dict[str, Any], ...]:
    """Kit 1.1.0 warm-up questions (questions_basic.jsonl)."""
    return tuple(_questions(split, "questions_basic.jsonl"))

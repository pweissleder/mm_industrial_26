"""The selectable people: a predefined list from configuration (no passwords, equal rights)."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


class ProfileConfigError(ValueError):
    pass


@dataclass(frozen=True)
class UserProfile:
    """A person (not a view mode such as Navigator/Developer, nor a clearance profile)."""

    id: str
    name: str
    initials: str
    color: str
    avatar: str | None = None
    # Where the person starts when they have no saved position yet:
    # `{dataset, endpoint, label?}` (see `PresenceService.seed`).
    start: dict[str, Any] | None = field(default=None, compare=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "initials": self.initials,
            "color": self.color,
            "avatar": self.avatar,
        }


def _initials(name: str) -> str:
    parts = [p for p in re.split(r"\s+", name.strip()) if p]
    return "".join(p[0] for p in parts[:2]).upper() or "?"


def load_profiles(path: Path) -> list[UserProfile]:
    """`[{id, name, color, initials?, avatar?, start?}]` from a JSON file; a missing file means
    none."""
    if not path.is_file():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProfileConfigError(f"{path}: {exc}") from exc
    if not isinstance(rows, list):
        raise ProfileConfigError(f"{path}: expected a list of profiles")
    profiles: list[UserProfile] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ProfileConfigError(f"{path}: every profile must be an object")
        ident, name, color = row.get("id"), row.get("name"), row.get("color")
        if not isinstance(ident, str) or not PROFILE_ID.match(ident) or ident in seen:
            raise ProfileConfigError(f"{path}: invalid or duplicate profile id {ident!r}")
        if not isinstance(name, str) or not name.strip():
            raise ProfileConfigError(f"{path}: profile {ident!r} needs a name")
        if not isinstance(color, str) or not COLOR.match(color):
            raise ProfileConfigError(f"{path}: profile {ident!r} needs a #rrggbb color")
        initials = row.get("initials")
        avatar = row.get("avatar")
        start = row.get("start")
        if start is not None and not (
            isinstance(start, dict)
            and isinstance(start.get("dataset"), str)
            and isinstance(start.get("endpoint"), dict)
        ):
            raise ProfileConfigError(f"{path}: profile {ident!r} start needs dataset and endpoint")
        profiles.append(
            UserProfile(
                id=ident,
                name=name.strip(),
                initials=initials if isinstance(initials, str) and initials else _initials(name),
                color=color.lower(),
                avatar=avatar if isinstance(avatar, str) and avatar else None,
                start=start,
            )
        )
        seen.add(ident)
    return profiles

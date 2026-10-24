"""Deterministic place search over rooms, assets, openings (exits flagged) and connectors.

Scoring is ported from the prototype assistant (`words` / `match_score` / `tool_find_places`): every
query word scores 1 when it is a word of the record text, 0.7 for a prefix match, 0.9 for a plural;
the score is the mean over the query words. Rows below 0.5 are dropped and only rows within 0.3 of
the best score are shown, best first (ties by name, then id).
"""

import difflib
import re
from collections.abc import Collection
from typing import Any

from app.navigation.errors import NavigationError

JSON = dict[str, Any]
OUTSIDE = "outside"
SEARCH_KINDS = ("room", "asset", "opening", "exit", "connector")
# Openings carry no names; they are searched when asked for, or when the query talks about doors.
DOOR_WORDS = frozenset(
    {"door", "doors", "opening", "openings", "entrance", "entrances", "exit", "exits", "emergency"}
)
CONNECTOR_SYNONYMS = {"lift": "lift elevator", "stairs": "stairs staircase stairway"}
MIN_SCORE = 0.5
SHOW_WINDOW = 0.3


def words(text: object) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:\.[0-9]+)*", str(text or "").lower().replace("_", " "))


def match_score(query_tokens: list[str], haystack: str) -> float:
    if not query_tokens:
        return 0.0
    hay = words(haystack)
    hits = 0.0
    for token in query_tokens:
        if token in hay:
            hits += 1
        elif any(
            h.startswith(token) or (len(token) > 3 and token.startswith(h) and len(h) > 2)
            for h in hay
        ):
            hits += 0.7
        elif len(token) > 4 and token.endswith("s") and token[:-1] in hay:
            hits += 0.9
    return hits / len(query_tokens)


def is_exit(opening: JSON) -> bool:
    return OUTSIDE in (opening.get("space_ids") or [])


def opening_name(opening: JSON, spaces: dict[str, JSON]) -> str:
    """Display name of an opening (the data has none): 'Exit from Lobby', 'Door between A and B'."""
    rooms = [str(spaces[i].get("name") or i) for i in opening.get("space_ids") or [] if i in spaces]
    if is_exit(opening):
        return "Exit from " + " and ".join(rooms)
    noun = "Door" if opening.get("kind") == "door" else "Opening"
    return f"{noun} between " + " and ".join(rooms)


def inside_space(opening: JSON, spaces: dict[str, JSON]) -> str | None:
    """The first real space of an opening (for an exit: the room it leads out of)."""
    return next((i for i in opening.get("space_ids") or [] if i in spaces), None)


def search(
    building: JSON,
    query: str,
    kind: str | None = None,
    floor_level: int | None = None,
    limit: int | None = 12,
    closed: Collection[str] = (),
) -> list[JSON]:
    tokens = words(query)
    if not tokens:
        raise NavigationError("invalid_input", "Give at least one search word")
    if kind is not None and kind not in SEARCH_KINDS:
        raise NavigationError(
            "invalid_input", "kind must be one of " + ", ".join(SEARCH_KINDS), {"kind": kind}
        )
    if limit is not None and limit < 1:
        raise NavigationError("invalid_input", "limit must be at least 1")
    floors = {f["id"]: f for f in building["floors"]}
    spaces = {s["id"]: s for s in building["spaces"]}
    rows: list[tuple[float, str, JSON]] = []

    def add(row: JSON, text: str) -> None:
        if floor_level is not None and floors.get(row["floor_id"], {}).get("level") != floor_level:
            return
        score = match_score(tokens, text)
        if score >= MIN_SCORE:
            rows.append((score, str(row["name"]), row))

    def floor_fields(floor_id: str | None) -> JSON:
        return {"floor_id": floor_id, "floor_level": floors.get(floor_id or "", {}).get("level")}

    if kind in (None, "room"):
        for s in building["spaces"]:
            text = " ".join(str(s.get(k) or "") for k in ("name", "ref", "room_type", "type", "id"))
            add(
                {
                    "kind": "room",
                    "id": s["id"],
                    "name": s.get("name") or s["id"],
                    "type": s.get("room_type") or s.get("type"),
                    **floor_fields(s["floor_id"]),
                    "space_id": s["id"],
                    "space_name": s.get("name") or s["id"],
                },
                text,
            )
    if kind in (None, "asset"):
        for a in building["assets"]:
            room = spaces.get(a.get("space_id") or "", {})
            text = " ".join(str(a.get(k) or "") for k in ("name", "type", "category", "id"))
            add(
                {
                    "kind": "asset",
                    "id": a["id"],
                    "name": a.get("name") or a["id"],
                    "type": a.get("type"),
                    **floor_fields(a.get("floor_id")),
                    "space_id": a.get("space_id"),
                    "space_name": room.get("name"),
                },
                text,
            )
    if kind in ("opening", "exit") or (kind is None and set(tokens) & DOOR_WORDS):
        for o in building["openings"]:
            exit_door = is_exit(o)
            if kind == "exit" and not exit_door:
                continue
            rooms = [str(spaces[i].get("name") or "") for i in o["space_ids"] if i in spaces]
            extra = ["exit", "entrance", "outside", "way", "out"] if exit_door else []
            text = " ".join([o.get("kind") or "", o["id"], "door", *rooms, *extra])
            inside = inside_space(o, spaces)
            add(
                {
                    "kind": "opening",
                    "id": o["id"],
                    "name": opening_name(o, spaces),
                    "type": o.get("kind"),
                    **floor_fields(o.get("floor_id")),
                    "space_id": inside,
                    "space_name": spaces.get(inside or "", {}).get("name"),
                    "is_exit": exit_door,
                    "width_m": o.get("width_m"),
                    "step_free": o.get("step_free"),
                },
                text,
            )
    if kind in (None, "connector"):
        for c in building["vertical_connectors"]:
            text = " ".join(
                [c.get("name") or "", CONNECTOR_SYNONYMS.get(c.get("kind") or "", ""), c["id"]]
            )
            floor_ids = list(c.get("floor_ids") or [])
            add(
                {
                    "kind": "connector",
                    "id": c["id"],
                    "name": c.get("name") or c["id"],
                    "type": c.get("kind"),
                    **floor_fields(floor_ids[0] if floor_ids else None),
                    "floor_levels": [floors[f]["level"] for f in floor_ids if f in floors],
                    "space_ids": list(c.get("space_ids") or []),
                    "step_free": c.get("step_free"),
                },
                text,
            )
    rows.sort(key=lambda r: (-r[0], r[1], r[2]["id"]))
    best = rows[0][0] if rows else 0.0
    shown = [r for r in rows if r[0] >= best - SHOW_WINDOW]
    if limit is not None:
        shown = shown[:limit]
    closed_set = set(closed)
    results = []
    for score, _, row in shown:
        row["score"] = round(score, 2)
        if row["id"] in closed_set:
            row["closed"] = True
        results.append(row)
    return results


def suggestions(building: JSON, query: str, n: int = 5) -> list[str]:
    """Close names or types when nothing matched ('did you mean')."""
    records = building["spaces"] + building["assets"]
    names = {str(x.get("name")) for x in records if x.get("name")} | {
        str(x.get("room_type") or x.get("type")).replace("_", " ") for x in records
    }
    return difflib.get_close_matches(" ".join(words(query)), sorted(names), n=n, cutoff=0.5)

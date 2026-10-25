"""OFFLINE agent memory: category keys, route statistics, the recall_memory tool and the
/api/memory endpoints (real app, real factory building, scripted Claude)."""

from pathlib import Path
from typing import Any

import pytest

from app.assistant.memory import AgentMemory, category_key
from app.assistant.service import _floor_changes
from app.assistant.tools import union_count
from app.config import Settings
from tests.assistant.test_assistant_integration import (
    OFFICE_F1,
    datasets_dir,  # noqa: F401  (fixture)
    ingest,  # noqa: F401  (fixture)
    make_client,
    turn,
)
from tests.assistant.test_assistant_unit import SID, make, update


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("room_type:toilet", "toilet"),
        ("room_type:Toilets", "toilet"),
        ("asset_type:coffee_machine", "coffee_machine"),
        ("Coffee machines", "coffee_machine"),
        ("exit", "exit"),
        ("query:glass", "glass"),
    ],
)
def test_category_key(raw: str, key: str) -> None:
    assert category_key(raw) == key


def route(start: str = "S1", target: str = "S2", **extra: Any) -> dict[str, Any]:
    return {
        "mode": "official",
        "reachable": True,
        "start": {"kind": "space", "id": start},
        "target": {"kind": "space", "id": target},
        "walk_m": 40.0,
        "length_m": 47.0,
    } | extra


def test_same_route_again_in_a_session_counts_once() -> None:
    memory = AgentMemory()
    assert memory.add_route("s1", "b1", route())
    assert not memory.add_route("s1", "b1", route())
    assert memory.add_route("s2", "b1", route())  # another conversation
    assert memory.add_route("s1", "b1", route(target="S3"))
    assert memory.add_route("s1", "b1", route())  # back again after another route
    assert memory.stats()["routes"]["count"] == 4


def test_route_stats_split_climb_by_stairs_and_lift() -> None:
    memory = AgentMemory()
    up = [{"kind": "stairs", "delta_m": 3.5}, {"kind": "lift", "delta_m": 7.0}]
    down = [{"kind": "lift", "delta_m": -4.0}, {"kind": "stairs", "delta_m": None}]
    memory.add_route("s1", "b1", route(floor_changes=up))
    memory.add_route("s1", "b1", route(target="S3", floor_changes=down, walk_m=None))
    memory.add_route("s1", "b2", route(target="S4", reachable=False, length_m=None))
    stats = memory.stats()["routes"]
    assert stats["count"] == 2  # the unreachable one is kept as an event but not counted
    assert stats["distance_m"] == 87.0  # walk_m, else length_m
    assert stats["official_cost_m"] == 94.0
    assert stats["floor_changes"] == {"stairs": 2, "lift": 2}
    assert stats["climbed_m"] == {"stairs": 3.5, "lift": 7.0}
    assert stats["descended_m"] == {"stairs": 0.0, "lift": 4.0}
    assert memory.stats()["by_building"] == {"b1": {"routes": 2, "distance_m": 87.0}}
    assert len(memory.recent_events("route")) == 3


def test_memory_is_bounded_and_clearable() -> None:
    memory = AgentMemory(max_messages=3, max_events=2)
    for i in range(5):
        memory.add_message("s1", "b1", "user", f"message {i}")
        memory.add_search("s1", "b1", "find_nearest", "room_type:toilet", 1)
    assert [m["text"] for m in memory.recent_messages()] == ["message 2", "message 3", "message 4"]
    assert memory.stats()["searches"] == {"total": 2, "by_category": {"toilet": 2}}
    memory.clear()
    assert memory.stats()["turns"] == 0 and memory.stats()["since"] is None


def test_floor_changes_are_signed_from_floor_elevations() -> None:
    data = {"floors": [{"id": "F0", "elevation_m": 0.0}, {"id": "F1", "elevation_m": 3.6}]}
    result = {
        "transitions": [
            {"kind": "door"},
            {"kind": "stairs", "from_floor_id": "F0", "to_floor_id": "F1"},
            {"kind": "lift", "from_floor_id": "F1", "to_floor_id": "F0"},
            {"kind": "lift", "from_floor_id": "F1", "to_floor_id": "F9"},
        ]
    }
    assert [(c["kind"], c["delta_m"]) for c in _floor_changes(data, result)] == [
        ("stairs", 3.6),
        ("lift", -3.6),
        ("lift", None),
    ]


def test_recall_memory_spans_conversations() -> None:
    script = [
        update(start={"kind": "space", "id": "S2"}),
        ("find_nearest", {"category": "room_type:toilets", "step_free": None, "and_route": False}),
        ("find_nearest", {"category": "exit", "step_free": None, "and_route": True}),
        "The exit is right by the lobby.",
        ("recall_memory", {"topic": "stats", "query": ""}),
        ("recall_memory", {"topic": "search_chats", "query": "toilet"}),
        "You looked for a toilet once.",
    ]
    assistant, _, _ = make(script)
    assert union_count(assistant.tools) <= 16
    assistant.turn(SID, "I'm in the lobby. Toilet? Otherwise the exit.", {"building_id": "b1"})
    assistant.reset(SID)  # "New conversation": session history goes, memory stays
    question = "How often did I look for a toilet?"
    out = assistant.turn("session-0002", question, {"building_id": "b1"})
    stats = out["tools"][0]["output"]["stats"]
    assert stats["searches"]["by_category"] == {"toilet": 1, "exit": 1}
    assert stats["routes"]["count"] == 1 and stats["routes"]["floor_changes"]["stairs"] == 1
    assert stats["sessions"] == 2 and stats["turns"] == 2
    found = out["tools"][1]["output"]["messages"]
    assert [m["session_id"] for m in found] == [SID, "session-0002"]
    assert all("[tools used" not in m["text"] for m in assistant.memory.recent_messages())


def test_memory_api_reports_planned_floor_changes(
    settings: Settings,
    datasets_dir: Path,  # noqa: F811
    ingest: Any,  # noqa: F811
) -> None:
    script = [
        update(start={"kind": "space", "id": OFFICE_F1}),
        ("find_nearest", {"category": "exit", "step_free": None, "and_route": True}),
        "The nearest exit is downstairs.",
    ]
    with make_client(settings, datasets_dir, script) as client:
        ingest(client)
        turn(client, "I'm in Office 1.04. Nearest exit?")
        stats = client.get("/api/memory/stats").json()
        events = client.get("/api/memory/events", params={"kind": "route"}).json()
        messages = client.get("/api/memory/messages", params={"q": "office"}).json()
        assert client.delete("/api/memory").status_code == 204
        cleared = client.get("/api/memory/stats").json()
    routes = stats["routes"]
    assert stats["searches"]["by_category"] == {"exit": 1}
    assert routes["count"] == 1 and routes["distance_m"] > 0
    assert sum(routes["floor_changes"].values()) >= 1
    assert sum(routes["descended_m"].values()) > 0 and sum(routes["climbed_m"].values()) == 0
    assert events[0]["start"] == {"kind": "space", "id": OFFICE_F1}
    assert [m["role"] for m in messages] == ["user"]
    assert cleared["routes"]["count"] == 0 and cleared["turns"] == 0

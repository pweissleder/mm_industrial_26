"""OFFLINE assistant unit tests: a scripted stand-in replaces Claude and a small fake replaces
NavigationService. Real-data behaviour is covered in test_assistant_integration.py."""

import copy
import json
from typing import Any

import pytest

from app.assistant.port import NavigationError
from app.assistant.service import MAX_ROUNDS, Assistant, AssistantError
from app.assistant.tools import union_count

BUILDING: dict[str, Any] = {
    "building": {"id": "b1", "name": "Test building", "source": "procedural"},
    "floors": [
        {"id": "F0", "level": 0, "name": "Ground floor"},
        {"id": "F-1", "level": -1, "name": "Basement"},
    ],
    "spaces": [
        {
            "id": "S1",
            "floor_id": "F-1",
            "name": "Toilet -1.01",
            "room_type": "toilet",
            "type": "room",
        },
        {"id": "S2", "floor_id": "F0", "name": "Lobby 0.01", "room_type": "lobby", "type": "room"},
    ],
    "openings": [
        {"id": "O1", "floor_id": "F0", "space_ids": ["S2", "outside"], "kind": "door"},
        {"id": "O2", "floor_id": "F0", "space_ids": ["S2", "S1"], "kind": "door"},
    ],
    "vertical_connectors": [{"id": "V1", "kind": "stairs", "name": "Stairs"}],
    "assets": [
        {
            "id": "A1",
            "name": "Hand dryer",
            "type": "hand_dryer",
            "space_id": "S1",
            "floor_id": "F-1",
        }
    ],
}


class FakeNavigation:
    """Test double of NavigationService with recorded calls (no geometry)."""

    def __init__(self) -> None:
        self.closed: list[str] = []
        self.revision = 0
        self.calls: list[tuple[str, Any]] = []

    def buildings(self) -> list[dict[str, Any]]:
        return [{"id": "b1", "name": "Test building"}, {"id": "b2", "name": "Other"}]

    def building(self, building_id: str) -> dict[str, Any]:
        return copy.deepcopy(BUILDING) | ({"building": {"id": building_id, "name": building_id}})

    def state(self, building_id: str) -> dict[str, Any]:
        if building_id not in {"b1", "b2"}:
            raise NavigationError("unknown_building", "unknown")
        return {
            "building_id": building_id,
            "revision": self.revision,
            "closed": list(self.closed),
            "support": {"official": True, "precision": True},
        }

    def search(
        self, building_id: str, query: str, kind: str | None, floor_level: int | None, limit: int
    ) -> list[dict[str, Any]]:
        self.calls.append(("search", query))
        if "dryer" in query:
            return [
                {
                    "kind": "asset",
                    "id": "A1",
                    "name": "Hand dryer",
                    "space_name": "Toilet -1.01",
                    "floor_level": -1,
                }
            ]
        return []

    def nearest(
        self, building_id: str, origin: dict[str, Any], category: str, step_free: bool, limit: int
    ) -> list[dict[str, Any]]:
        self.calls.append(("nearest", category))
        if category == "exit":
            return [
                {
                    "endpoint": {"kind": "opening", "id": "O1"},
                    "name": "Exit from Lobby 0.01",
                    "walk_cost_m": 14.0,
                }
            ]
        return []

    def route(self, building_id: str, request: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("route", request))
        return {
            "mode": request["mode"],
            "status": "ok",
            "reachable": True,
            "checked": "official_rules",
            "length_m": 14.0,
            "space_path": ["S1", "S2"],
            "element_path": ["V1", "O1"],
            "transitions": [{"kind": "stairs", "schematic": False}],
            "directions": ["Use the stairs to the ground floor.", "Walk to the exit door."],
        }

    def preview_closure(
        self,
        building_id: str,
        element_ids: list[str],
        origin: dict[str, Any] | None,
        step_free: bool,
    ) -> dict[str, Any]:
        return {"rooms_unreachable_after": 1, "unreachable_ids": ["S1"]}

    def change(
        self, building_id: str, action: str, element_id: str | None, expected_revision: int
    ) -> dict[str, Any]:
        if expected_revision != self.revision:
            raise NavigationError("stale_revision", "stale")
        self.revision += 1
        if action == "close" and element_id:
            self.closed.append(element_id)
        return self.state(building_id)

    def operation(
        self, building_id: str, operation: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(("operation", operation))
        return {"operation": operation, "value": 1, "formatted_answer": "Answer: 1"}

    def operation_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "largest_room",
                "fields": [{"name": "floor_id", "type": "floor", "required": True}],
            }
        ]


class Scripted:
    """Offline stand-in for Claude: replays tool calls, then text; records every request."""

    def __init__(self, script: list[Any]) -> None:
        self.script, self.requests = list(script), []

    def create(self, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(json.loads(json.dumps(kwargs)))
        if kwargs["tool_choice"]["type"] == "none":
            return {
                "stop_reason": "end_turn",
                "usage": {},
                "content": [
                    {
                        "type": "text",
                        "text": "The closest exit is through the lobby. Shall I show the way?",
                    }
                ],
            }
        step = self.script.pop(0)
        if isinstance(step, str):
            return {
                "stop_reason": "end_turn",
                "usage": {},
                "content": [{"type": "text", "text": step}],
            }
        name, args = step
        return {
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 5, "output_tokens": 5},
            "content": [
                {"type": "thinking", "thinking": "", "signature": "x"},
                {"type": "tool_use", "id": f"t{len(self.requests)}", "name": name, "input": args},
            ],
        }


SID = "session-0001"


def make(script: list[Any]) -> tuple[Assistant, FakeNavigation, Scripted]:
    nav, transport = FakeNavigation(), Scripted(script)
    return Assistant(nav, transport), nav, transport


def update(**values: Any) -> tuple[str, dict[str, Any]]:
    args = {
        "start": None,
        "destination": None,
        "clear_start": False,
        "clear_destination": False,
        "mode": None,
        "step_free": None,
        "profile": None,
        "width_m": None,
        "safety_m": None,
    }
    return ("update_session", args | values)


def test_tool_schemas_strict_and_within_union_limit() -> None:
    assistant, _, _ = make([])
    assert union_count(assistant.tools) <= 16
    for t in assistant.tools:
        schema = t["input_schema"]
        assert t["strict"] and schema["additionalProperties"] is False
        assert sorted(schema["required"]) == sorted(schema["properties"])


def test_case_a_exit_door_is_a_valid_destination_and_route_ends_there() -> None:
    assistant, nav, _ = make(
        [
            ("find_places", {"query": "hand dryer", "kind": "equipment", "floor_level": None}),
            update(start={"kind": "space", "id": "S1"}),
            ("find_nearest", {"category": "exit", "step_free": None, "and_route": True}),
            "The nearest exit is the lobby door, up the stairs.",
        ]
    )
    out = assistant.turn(
        SID,
        "I'm in the basement next to a hand dryer. Where is the nearest exit?",
        {"building_id": "b1"},
    )
    route = next(c for c in nav.calls if c[0] == "route")[1]
    assert route["target"] == {"kind": "opening", "id": "O1"}
    assert out["ui"]["route"]["element_path"][-1] == "O1"
    assert out["phases"][-1] == "Showing directions"
    # Opening IDs are accepted directly too (the earlier failure rejected them as asset/space IDs).
    assistant2, nav2, _ = make(
        [
            update(start={"kind": "space", "id": "S1"}),
            (
                "plan_route",
                {
                    "start": None,
                    "destination": {"kind": "opening", "id": "O1"},
                    "mode": None,
                    "step_free": None,
                    "assume_closed": [],
                },
            ),
            "ok",
        ]
    )
    assert assistant2.turn(SID, "route to O1", {"building_id": "b1"})["tools"][1]["status"] == "ok"


def test_case_b_budget_ends_with_a_normal_answer_not_a_limit_message() -> None:
    loop = [
        ("find_places", {"query": f"lift {i}", "kind": None, "floor_level": None})
        for i in range(MAX_ROUNDS)
    ]
    assistant, nav, transport = make(loop)
    out = assistant.turn(
        SID, "I don't know, the biggest one in this building.", {"building_id": "b1"}
    )
    assert out["status"] == "budget_reached"
    assert "steps" not in out["reply"].lower() and out["reply"].endswith("?")
    assert transport.requests[-1]["tool_choice"] == {"type": "none"}
    assert len(transport.requests) == MAX_ROUNDS + 1
    # Searches alone never changed start/destination.
    assert out["state"]["start"] is None and out["state"]["destination"] is None


def test_search_does_not_change_endpoints_and_unknown_ids_are_rejected() -> None:
    assistant, _, _ = make(
        [
            ("find_places", {"query": "hand dryer", "kind": None, "floor_level": None}),
            (
                "plan_route",
                {
                    "start": {"kind": "asset", "id": "NOPE"},
                    "destination": None,
                    "mode": None,
                    "step_free": None,
                    "assume_closed": [],
                },
            ),
            "Which room are you in?",
        ]
    )
    out = assistant.turn(SID, "there's a hand dryer here", {"building_id": "b1"})
    assert out["state"]["start"] is None
    assert (
        out["tools"][1]["status"] == "error"
        and out["tools"][1]["output"]["error"] == "unknown_place"
    )
    assert "unknown_place" not in out["reply"]


def test_nearest_without_start_asks_for_position() -> None:
    assistant, _, _ = make(
        [
            (
                "find_nearest",
                {"category": "room_type:toilet", "step_free": None, "and_route": True},
            ),
            "Where are you right now?",
        ]
    )
    out = assistant.turn(SID, "Find the closest restroom.", {"building_id": "b1"})
    assert out["tools"][0]["output"]["error"] == "missing_start"


def test_small_talk_needs_no_tool() -> None:
    assistant, nav, _ = make(["Why did the lift break up with the stairs? It needed space."])
    out = assistant.turn(SID, "Tell me a joke.", {"building_id": "b1"})
    assert out["tools"] == [] and out["phases"] == [] and nav.calls == []


def test_change_needs_confirmation_and_stale_revision_is_refused() -> None:
    assistant, nav, _ = make(
        [
            ("propose_change", {"action": "close", "element_id": "V1"}),
            "Press Confirm to close the stairs.",
        ]
    )
    out = assistant.turn(SID, "Close the stairs.", {"building_id": "b1"})
    proposal = out["ui"]["proposal"]
    assert nav.closed == []
    assert assistant.confirm(SID, proposal["proposal_id"], True)["status"] == "applied"
    assert nav.closed == ["V1"]
    assistant2, nav2, _ = make(
        [("propose_change", {"action": "close", "element_id": "S2"}), "Confirm?"]
    )
    p2 = assistant2.turn(SID, "close the lobby", {"building_id": "b1"})["ui"]["proposal"]
    nav2.revision += 1
    with pytest.raises(NavigationError):
        assistant2.confirm(SID, p2["proposal_id"], True)


def test_building_change_clears_context() -> None:
    assistant, _, transport = make(["Hello!", "Hi again."])
    assistant.turn(
        SID, "first message", {"building_id": "b1", "start": {"kind": "space", "id": "S2"}}
    )
    assistant.turn(SID, "second", {"building_id": "b2"})
    second = transport.requests[-1]["messages"]
    assert len(second) == 1 and "first message" not in second[0]["content"]
    assert assistant.session(SID).state["start"] is None


def test_history_is_bounded_and_plain_text() -> None:
    assistant, _, transport = make(["ok"] * 20)
    for i in range(20):
        assistant.turn(SID, f"question {i}", {"building_id": "b1"})
    messages = transport.requests[-1]["messages"]
    assert len(messages) <= 13 and all(isinstance(m["content"], str) for m in messages)


def test_offline_without_key_is_explicit() -> None:
    with pytest.raises(AssistantError):
        Assistant(FakeNavigation()).turn(SID, "hello", {"building_id": "b1"})

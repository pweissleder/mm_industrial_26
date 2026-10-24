"""The nine benchmark operations: specs, synthetic contracts (ported from the prototype's
test_operations.py) and the service wrapper with scenario closures on the in-repo building."""

import copy
from typing import Any

import pytest

from app.navigation import NavigationService
from app.navigation.errors import NavigationError
from app.navigation.official import building_map as official_map
from app.navigation.official import score
from app.navigation.ported.operations import compute, format_answer
from tests.navigation.conftest import BID, P

NINE = {
    "largest_room",
    "floor_area_total",
    "errand",
    "wide_delivery_count",
    "step_free_reachable_count",
    "room_stacked",
    "shared_walls_count",
    "straight_line_3d",
    "what_if_door",
}


def room(
    sid: str,
    x: float = 0,
    y: float = 0,
    width: float = 4,
    depth: float = 4,
    floor: str = "F0",
    kind: str = "room",
    room_type: str = "office",
) -> dict[str, Any]:
    return {
        "id": sid,
        "floor_id": floor,
        "name": "Room " + sid,
        "ref": sid,
        "type": kind,
        "room_type": room_type,
        "polygon": [[x, y], [x + width, y], [x + width, y + depth], [x, y + depth]],
        "synthetic": False,
    }


def synthetic() -> dict[str, Any]:
    def door(oid: str, a: str, b: str, pos: list[float], width: float, kind: str = "door") -> Any:
        return {
            "id": oid,
            "floor_id": "F0",
            "space_ids": [a, b],
            "kind": kind,
            "position": pos,
            "width_m": width,
            "step_free": True,
        }

    return {
        "building": {"id": "synthetic", "source": "procedural", "units": "m"},
        "floors": [
            {"id": "F0", "level": 0, "name": "Ground", "elevation_m": 0},
            {"id": "F1", "level": 1, "name": "First", "elevation_m": 3},
        ],
        "spaces": [
            room("A"),
            room("B", 4),
            room("C", 8),
            room("H", 0, 4, kind="hall"),
            room("Z", floor="F1"),
        ],
        "openings": [
            door("AB", "A", "B", [4, 2], 1.1),
            door("BC", "B", "C", [8, 2], 0.8),
            door("entrance", "A", "outside", [0, 2], 1.2, "entrance"),
        ],
        "vertical_connectors": [
            {
                "id": "lift",
                "name": "Lift",
                "kind": "lift",
                "floor_ids": ["F0", "F1"],
                "space_ids": ["A", "Z"],
                "step_free": True,
            }
        ],
        "assets": [
            {
                "id": "item",
                "name": "Pickup",
                "type": "chair",
                "space_id": "B",
                "floor_id": "F0",
                "position": [5, 2],
            }
        ],
    }


def test_specs_cover_nine_without_question_metadata(nav: NavigationService) -> None:
    specs = nav.operation_specs()
    assert {s["name"] for s in specs} == NINE and len(specs) == 9
    assert all(s["fields"] for s in specs)
    names = {f["name"] for s in specs for f in s["fields"]}
    assert not names & {"question", "answer", "kind", "inputs"}


def test_area_largest_and_input_validation() -> None:
    b = synthetic()
    b["spaces"][1] = room("B", 4, width=6)
    result = compute(b, "largest_room", {"floor_id": "F0", "room_type": "office"})
    assert (result["space_id"], result["value"]) == ("B", 24)
    assert compute(b, "floor_area_total", {"floor_id": "F0", "room_type": "missing"})["value"] == 0
    with pytest.raises(NavigationError):
        compute(b, "largest_room", {"floor_id": "F0", "room_type": "missing"})
    for bad in (float("nan"), True):
        with pytest.raises(NavigationError):
            compute(b, "wide_delivery_count", {"start": "A", "floor_id": "F0", "min_width": bad})


def test_width_filter_and_scenario_closures() -> None:
    b = synthetic()
    before = copy.deepcopy(b)
    args = {"start": "A", "floor_id": "F0", "min_width": 1.1}
    assert compute(b, "wide_delivery_count", args)["ids"] == ["A", "B"]
    assert compute(b, "wide_delivery_count", {**args, "_scenario_closed": ["AB"]})["ids"] == ["A"]
    sf = compute(b, "step_free_reachable_count", {"start": "A", "floor_id": "F1", "closed": "lift"})
    assert sf["value"] == 0
    with pytest.raises(NavigationError):
        compute(b, "wide_delivery_count", {**args, "min_width": 1, "_scenario_closed": ["A"]})
    assert b == before


def test_errand_uses_official_route_lengths() -> None:
    b = synthetic()
    args = {"start": "A", "asset_type": "chair", "floor_id": "F0", "room_type": "office"}
    errand = compute(b, "errand", args)
    assert (errand["via"], errand["target"], errand["value"]) == ("B", "A", 8)
    pickup = official_map.BuildingMap(copy.deepcopy(b)).route("A", "B").length_m
    assert errand["trace"]["pickup_length_m"] == pytest.approx(pickup, abs=0.005)
    with pytest.raises(NavigationError):
        compute(b, "errand", {**args, "_scenario_closed": ["AB"]})


def test_stacked_shared_walls_and_3d() -> None:
    b = synthetic()
    assert compute(b, "room_stacked", {"start": "A", "direction": "above"})["space_id"] == "Z"
    with pytest.raises(NavigationError):
        compute(b, "room_stacked", {"start": "A", "direction": "sideways"})
    b["spaces"] += [room("touch", x=4, y=-4), room("corridor", x=-4, kind="corridor")]
    assert set(compute(b, "shared_walls_count", {"start": "A"})["ids"]) == {"B", "H"}
    assert compute(b, "straight_line_3d", {"first": "A", "second": "Z"})["value"] == 3


def test_what_if_door_strict_improvement() -> None:
    b = synthetic()
    b["openings"] = [o for o in b["openings"] if o["id"] != "AB"]
    before = copy.deepcopy(b)
    result = compute(b, "what_if_door", {"first": "A", "second": "B", "floor_id": "F0"})
    assert set(result["ids"]) == {"B", "C"}
    assert b == before
    unchanged = compute(
        synthetic(),
        "what_if_door",
        {"first": "A", "second": "B", "floor_id": "F0", "improvement_m": 0},
    )
    assert unchanged["value"] == 0


def test_formatted_answer_scores_with_official_scorer() -> None:
    answer = format_answer("shared_walls_count", {"operation": "shared_walls_count", "value": 2})
    expected = {"type": "number", "value": 2, "relative_tolerance": 0, "absolute_tolerance": 0}
    assert score.score_reply(expected, answer)
    stacked = {
        "operation": "room_stacked",
        "space_id": "Z",
        "space_name": "Büro",
        "space_ref": "1.02",
        "value": "Z",
    }
    assert format_answer("room_stacked", stacked) == "Answer: 1.02"


def test_service_operation_applies_scenario_closures(fresh_nav: NavigationService) -> None:
    nav = fresh_nav
    area = nav.operation(BID, "floor_area_total", {"floor_id": P + "F0", "room_type": "office"})
    assert area["value"] > 0 and area["formatted_answer"].startswith("Answer:")
    assert area["revision"] == 0
    args = {"start": P + "F0-S021", "floor_id": P + "F1"}
    before = nav.operation(BID, "step_free_reachable_count", args)["value"]
    assert before > 0
    nav.change(BID, "close", P + "V02", 0)
    after = nav.operation(BID, "step_free_reachable_count", args)
    assert (after["value"], after["revision"]) == (0, 1)
    with pytest.raises(NavigationError) as caught:
        nav.operation(BID, "nonsense", {})
    assert caught.value.code == "invalid_input"

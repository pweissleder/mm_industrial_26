"""Regression: a route that rides the stairs over several floors without walking on the
intermediate floors (proc-campus-dev-001, F3 room -> F0 exit door, found by
scripts/e2e/api_matrix.py) drew the F0 walk before the last two stair rides, so the drawn route
and its waypoints ended at the stairs instead of at the exit door."""

from typing import Any

from app.navigation.response import official_response

FLOORS = [{"id": f"F{i}", "level": i, "elevation_m": 3.8 * i} for i in range(4)]
STAIRS = (11.93, 3.0)


def ride(src: int, dst: int) -> dict[str, Any]:
    return {
        "kind": "stairs",
        "element_id": "V01",
        "from_floor": f"F{src}",
        "to_floor": f"F{dst}",
        "from_space": f"F{src}-S4",
        "to_space": f"F{dst}-S4",
        "from_position": list(STAIRS),
        "to_position": list(STAIRS),
        "cost_m": 7.6,
    }


def result() -> dict[str, Any]:
    end = {"kind": "opening", "id": "F0-EXIT", "space_id": "F0-S1", "floor_id": "F0"}
    return {
        "status": "ok",
        "reachable": True,
        "cost_m": 45.0,
        "walk_m": 22.2,
        "vertical_cost_m": 22.8,
        "start": {
            "kind": "space",
            "id": "F3-S14",
            "space_id": "F3-S14",
            "floor_id": "F3",
            "position": [3.4, 11.4],
        },
        "target": end | {"position": [0.0, 7.2]},
        "space_path": ["F3-S14", "F3-S4", "F2-S4", "F1-S4", "F0-S4", "F0-S1"],
        "element_path": ["F3-D1", "V01", "V01", "V01", "F0-EXIT"],
        "segments": [
            {"floor_id": "F3", "space_id": "F3-S14", "points": [[3.4, 11.4], list(STAIRS)]},
            {"floor_id": "F0", "space_id": "F0-S4", "points": [list(STAIRS), [0.0, 7.2]]},
        ],
        "transitions": [ride(3, 2), ride(2, 1), ride(1, 0)],
        "directions": [],
        "legs": [],
        "rules": {},
        "kit": {},
        "steps": [],
        "warnings": [],
        "assumptions": [],
    }


BUILDING = {
    "floors": FLOORS,
    "openings": [
        {"id": "F3-D1", "floor_id": "F3", "space_ids": ["F3-S14", "F3-S4"], "position": [3.4, 8.4]},
        {
            "id": "F0-EXIT",
            "floor_id": "F0",
            "space_ids": ["F0-S1", "outside"],
            "position": [0.0, 7.2],
        },
    ],
}


def test_all_stair_rides_come_before_the_walk_on_the_arrival_floor() -> None:
    route = official_response(result(), BUILDING)
    kinds = [(seg["kind"], seg["floor_id"], seg.get("to_floor_id")) for seg in route["path"]]
    assert kinds == [
        ("walk", "F3", None),
        ("connector", "F3", "F2"),
        ("connector", "F2", "F1"),
        ("connector", "F1", "F0"),
        ("walk", "F0", None),
    ]
    assert route["path"][-1]["points"][-1] == [0.0, 7.2]
    last = route["waypoints"][-1]
    assert last["kind"] == "target" and last["position"] == [0.0, 7.2]
    assert last["element_id"] == "F0-EXIT" and last["fraction"] == 1.0
    vertical = [t for t in route["transitions"] if t["kind"] == "stairs"]
    assert [(t["from_floor_id"], t["to_floor_id"]) for t in vertical] == [
        ("F3", "F2"),
        ("F2", "F1"),
        ("F1", "F0"),
    ]
    assert route["transitions"][-1]["element_id"] == "F0-EXIT"

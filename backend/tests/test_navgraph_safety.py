"""Regression tests for the navgraph review: closures, strict rules, geometry failures,
closure persistence and endpoint validation. Synthetic buildings keep each case obvious."""

import math
from pathlib import Path
from typing import Any

import pytest
from shapely.geometry import MultiPolygon, box

from navgraph import Building, NavGraph, OrganizerCost, rules
from navgraph.agent import BuildingIntelligence
from navgraph.geometry import WalkableArea
from navgraph.model import OUTSIDE
from navgraph.scoring import robust_route, validate


def _space(sid: str, x0: float, x1: float, **kw: Any) -> dict[str, Any]:
    return {
        "id": sid,
        "floor_id": "F0",
        "type": kw.get("type", "room"),
        "room_type": kw.get("room_type", "office"),
        "name": f"Room {sid}",
        "ref": sid,
        "polygon": [[x0, 0], [x1, 0], [x1, 4], [x0, 4]],
    }


def _door(oid: str, a: str, c: str, x: float) -> dict[str, Any]:
    return {
        "id": oid,
        "floor_id": "F0",
        "space_ids": [a, c],
        "kind": "door",
        "position": [x, 2],
        "width_m": 1.0,
        "step_free": True,
    }


def _chain(middle: dict[str, Any] | None = None) -> Building:
    """A | B | C side by side: the only way from A to C is through B."""
    return Building.from_dict(
        {
            "building": {"id": "t", "name": "Test"},
            "floors": [{"id": "F0", "level": 0, "name": "Ground", "elevation_m": 0, "height_m": 3}],
            "spaces": [_space("A", 0, 4), middle or _space("B", 4, 8), _space("C", 8, 12)],
            "openings": [
                _door("AB", "A", "B", 4),
                _door("BC", "B", "C", 8),
                _door("EA", "A", OUTSIDE, 0),
            ],
            "vertical_connectors": [],
            "assets": [],
        }
    )


@pytest.mark.parametrize("cost", [OrganizerCost(), None])
def test_closed_room_is_never_crossed(cost: OrganizerCost | None) -> None:
    b = _chain()
    b.set_closed("B")
    nav = NavGraph(b, cost)

    assert not nav.route("A", "C").reachable
    assert nav.distances_from("A") == {"A": 0.0}
    assert nav.nearest("A", ["C"]) == (None, math.inf)
    assert "C" not in nav.rooms.reachable("A")
    assert "B" not in {n["space"] for n in nav.rooms.neighbours("A")}
    assert validate(b, ["A", "B", "C"], rules.WALK)


def test_closed_endpoint_is_unreachable() -> None:
    b = _chain()
    b.set_closed("C")
    nav = NavGraph(b)

    assert not nav.route("A", "C").reachable
    assert not nav.route("C", "C").reachable
    assert nav.nearest("C", ["C"]) == (None, math.inf)


def test_same_endpoints_are_validated() -> None:
    nav = NavGraph(_chain())

    with pytest.raises(KeyError):
        nav.route("missing", "missing")
    assert nav.route("A", "A").reachable


def test_robust_route_never_relaxes_the_bay_rule() -> None:
    b = _chain(_space("B", 4, 8, type="hall", room_type="production_zone"))
    nav = NavGraph(b, OrganizerCost())
    trolley = rules.wide_load(0.5)

    assert not nav.route("A", "C", trolley).reachable
    assert not robust_route(nav, "A", "C", trolley).route.reachable


def test_walkers_cross_bays_vehicles_do_not() -> None:
    bi = BuildingIntelligence(_chain(_space("B", 4, 8, type="hall", room_type="production_zone")))

    assert bi.call("route", {"start": "A", "goal": "C"})["spaces"] == ["A", "B", "C"]
    assert bi.call("route", {"start": "A", "goal": "C", "profile": "wide_load", "min_width": 0.5})[
        "reachable"
    ] is False


def test_closures_survive_save_and_load(tmp_path: Path) -> None:
    b = _chain()
    b.set_closed("B")
    b.save(tmp_path / "building.json")

    assert Building.load(tmp_path).closed == {"B"}


def test_disconnected_area_is_unreachable_not_recursive() -> None:
    area = WalkableArea(MultiPolygon([box(0, 0, 4, 4), box(10, 0, 14, 4)]))

    assert area.distance((2, 2), (12, 2)) == math.inf
    assert area.distance((1, 1), (3, 3)) == pytest.approx(math.dist((1, 1), (3, 3)))

"""Official (kit 1.1.0) routing through NavigationService.route on the in-repo factory building.

Ported from the prototype's test_challenge.py / test_service.py (challenge mode), adapted to the
Phase A RouteRequest/RouteResponse contract (docs/PHASE_A_INTERFACES.md).
"""

import copy
import json
import math
from collections.abc import Callable, Iterable
from typing import Any

import pytest

from app.navigation import NavigationService
from app.navigation.errors import NavigationError
from app.navigation.official import KIT_COMMIT, KIT_VERSION
from app.navigation.official import building_map as official_map
from tests.navigation.conftest import BID, P, space

Route = Callable[..., dict[str, Any]]
WAYPOINT_KEYS = {
    "index",
    "floor_id",
    "elevation_m",
    "position",
    "heading_deg",
    "kind",
    "distance_m",
    "fraction",
}


def official(
    building: dict[str, Any],
    start: str,
    target: str,
    step_free: bool = False,
    closed: Iterable[str] = (),
    via: list[str] | None = None,
) -> official_map.RouteResult:
    """The vendored, unchanged kit BuildingMap.route on a private copy."""
    bmap = official_map.BuildingMap(copy.deepcopy(building))
    for element in closed:
        bmap.set_closed(element, True)
    return bmap.route(start, target, step_free, via=via)


def assert_official(
    response: dict[str, Any], building: dict[str, Any], start: str, target: str, **kw: Any
) -> None:
    """Shape, internal consistency and identity with the official BuildingMap.route."""
    assert (response["mode"], response["status"], response["reachable"]) == ("official", "ok", True)
    assert response["checked"] == "official_rules"
    expected = official(building, start, target, **kw)
    assert response["length_m"] == expected.length_m
    assert response["space_path"] == expected.space_path
    assert (response["space_path"][0], response["space_path"][-1]) == (start, target)
    openings = {o["id"] for o in building["openings"]}
    connectors = {c["id"] for c in building["vertical_connectors"]}
    assert all(e in openings or e in connectors for e in response["element_path"])
    total = response["walk_m"] + response["vertical_cost_m"]
    assert response["length_m"] == pytest.approx(total, abs=0.005 + 1e-9)
    for step in response["steps"]:
        if step.get("type") == "walk" and step.get("clear_walk"):
            # Drawn geometry replays the official walk: equal to the 3-decimal edge weight.
            assert step["drawn_length_m"] == pytest.approx(step["cost_m"], abs=6e-4)
    json.dumps(response, allow_nan=False)


def test_same_floor_route_matches_official(route: Route, building: dict[str, Any]) -> None:
    res = route(space("F0-S021"), space("F0-S030"))
    assert_official(res, building, P + "F0-S021", P + "F0-S030")
    assert res["space_path"] == [P + "F0-S021", P + "F0-S016", P + "F0-S030"]
    assert res["element_path"] == [P + "F0-O0026", P + "F0-O0035"]
    assert res["vertical_cost_m"] == 0.0
    assert [t["kind"] for t in res["transitions"]] == ["door", "door"]
    assert res["kit"] == {"version": KIT_VERSION, "commit": KIT_COMMIT}
    assert res["revision"] == 0 and res["closed"] == []
    # Router text only: one "... through the ..." line per door crossed.
    through = [d for d in res["directions"] if " through the " in d]
    assert len(through) == len(res["element_path"])


def test_multi_floor_route_transitions_and_waypoints(
    route: Route, building: dict[str, Any]
) -> None:
    res = route(space("F0-S021"), space("F1-S010"))
    assert_official(res, building, P + "F0-S021", P + "F1-S010")
    assert res["length_m"] == 56.94
    assert res["vertical_cost_m"] == pytest.approx(7.0)
    [stairs] = [t for t in res["transitions"] if t["kind"] != "door"]
    assert (stairs["kind"], stairs["element_id"]) == ("stairs", P + "V01")
    assert (stairs["from_floor_id"], stairs["to_floor_id"]) == (P + "F0", P + "F1")
    assert stairs["cost_m"] == pytest.approx(7.0)  # 2 m per metre of a 3.5 m rise
    assert stairs["schematic"] is True and stairs["position"] == stairs["to_position"]
    # Transitions follow the element path order.
    assert [t["element_id"] for t in res["transitions"]] == res["element_path"]
    kinds = [p["kind"] for p in res["path"]]
    assert "connector" in kinds and set(kinds) <= {"walk", "door", "connector"}
    for segment in res["path"]:
        assert {"floor_id", "space_id", "kind", "points"} <= set(segment)
    wps = res["waypoints"]
    assert all(set(w) >= WAYPOINT_KEYS for w in wps)
    assert [w["index"] for w in wps] == list(range(len(wps)))
    assert (wps[0]["kind"], wps[-1]["kind"]) == ("start", "target")
    assert (wps[0]["fraction"], wps[-1]["fraction"]) == (0.0, 1.0)
    assert wps[0]["floor_id"] == P + "F0" and wps[-1]["floor_id"] == P + "F1"
    assert wps[0]["elevation_m"] == 0.0 and wps[-1]["elevation_m"] == pytest.approx(3.5)
    assert wps[-1]["distance_m"] == pytest.approx(res["length_m"], abs=0.01)
    fractions = [w["fraction"] for w in wps]
    assert fractions == sorted(fractions)
    assert any(w["kind"] == "stairs" and w.get("element_id") == P + "V01" for w in wps)
    assert res["start"]["position"] == wps[0]["position"]
    assert res["target"]["position"] == wps[-1]["position"]


def test_lift_when_stairs_closed_and_step_free(route: Route, building: dict[str, Any]) -> None:
    stairs_closed = route(space("F0-S021"), space("F1-S010"), assume_closed=[P + "V01"])
    assert_official(stairs_closed, building, P + "F0-S021", P + "F1-S010", closed=[P + "V01"])
    [lift] = [t for t in stairs_closed["transitions"] if t["kind"] != "door"]
    assert (lift["kind"], lift["element_id"], lift["cost_m"]) == ("lift", P + "V02", 18.5)
    step_free = route(space("F0-S021"), space("F1-S010"), step_free=True)
    assert_official(step_free, building, P + "F0-S021", P + "F1-S010", step_free=True)
    vertical = [t["kind"] for t in step_free["transitions"] if t["kind"] != "door"]
    assert vertical == ["lift"]
    assert step_free["rules"]["outside_link"] is False


def test_step_free_unreachable_is_a_response_with_diagnosis(route: Route) -> None:
    res = route(space("F0-S021"), space("F1-S010"), step_free=True, assume_closed=[P + "V02"])
    assert (res["status"], res["reachable"], res["length_m"]) == ("unreachable", False, None)
    assert "step-free" in res["unreachable_reason"]
    assert (res["space_path"], res["element_path"], res["waypoints"]) == ([], [], [])
    assert res["diagnosis"]["category"] == "closed"
    assert res["diagnosis"]["elements"] == [{"id": P + "V02"}]


def test_closures_detour_unreachable_and_blocked(route: Route, building: dict[str, Any]) -> None:
    base = route(space("F0-S021"), space("F1-S010"))
    detour = route(space("F0-S021"), space("F1-S010"), assume_closed=[P + "F0-S017"])
    assert_official(detour, building, P + "F0-S021", P + "F1-S010", closed=[P + "F0-S017"])
    assert P + "F0-S017" not in detour["space_path"]
    assert detour["length_m"] > base["length_m"]
    assert detour["assume_closed"] == [P + "F0-S017"]

    door = route(space("F0-S021"), space("F0-S030"), assume_closed=[P + "F0-O0026"])
    assert door["status"] == "unreachable"
    assert P + "F0-O0026" in door["unreachable_reason"]
    assert door["diagnosis"]["category"] == "closed"

    corridor = route(space("F0-S021"), space("F0-S030"), assume_closed=[P + "F0-S016"])
    assert (corridor["status"], corridor["reachable"]) == ("unreachable", False)
    assert P + "F0-S016" in corridor["unreachable_reason"]

    end = route(space("F0-S021"), space("F0-S030"), assume_closed=[P + "F0-S030"])
    assert (end["status"], end["reachable"], end["length_m"]) == ("blocked", False, None)
    assert end["diagnosis"]["elements"] == [{"id": P + "F0-S030"}]


def test_route_to_exit_door_ends_at_the_door(route: Route, building: dict[str, Any]) -> None:
    exit_id = P + "F0-O0054"
    exit_door = next(o for o in building["openings"] if o["id"] == exit_id)
    assert "outside" in exit_door["space_ids"]
    res = route(space("F0-S021"), {"kind": "opening", "id": exit_id})
    assert (res["status"], res["reachable"]) == ("ok", True)
    assert res["element_path"][-1] == exit_id
    assert "outside" not in res["space_path"]
    assert res["space_path"][-1] == P + "F0-S016"  # the room inside the exit
    assert res["target"]["kind"] == "opening" and res["target"]["is_exit"] is True
    assert res["target"]["position"] == exit_door["position"]
    assert res["path"][-1]["points"][-1] == exit_door["position"]
    assert res["waypoints"][-1]["position"] == exit_door["position"]
    assert res["transitions"][-1]["element_id"] == exit_id
    assert res["transitions"][-1]["is_exit"] is True
    # Exits are valid starts too: the route starts at the door itself (a start door is not
    # "passed", so it is not repeated in element_path).
    back = route({"kind": "opening", "id": exit_id}, space("F0-S021"))
    assert back["reachable"] and back["start"]["is_exit"] is True
    assert back["path"][0]["points"][0] == exit_door["position"]
    assert back["waypoints"][0]["position"] == exit_door["position"]
    assert back["space_path"] == [P + "F0-S016", P + "F0-S021"]


def test_route_to_asset_ends_in_its_room(route: Route, building: dict[str, Any]) -> None:
    asset = next(a for a in building["assets"] if a["space_id"] == P + "F0-S021")
    res = route(space("F0-S030"), {"kind": "asset", "id": asset["id"]})
    assert_official(res, building, P + "F0-S030", P + "F0-S021")
    assert res["target"]["kind"] == "asset" and res["target"]["id"] == asset["id"]
    assert res["target"]["requested_position"] == asset["position"]
    anchor = official_map.BuildingMap(copy.deepcopy(building)).anchor(P + "F0-S021")
    assert res["target"]["position"] == pytest.approx(list(anchor))


def test_opening_and_point_endpoints(route: Route, building: dict[str, Any]) -> None:
    door = route({"kind": "opening", "id": P + "F0-O0026"}, space("F0-S030"))
    assert door["reachable"] and door["start"]["kind"] == "opening"
    assert door["start"]["position"] == [5.0, 12.3]
    assert door["path"][0]["points"][0] == [5.0, 12.3]
    assert door["element_path"] == [P + "F0-O0035"]  # walks off into the corridor side
    sided = route(
        {"kind": "opening", "id": P + "F0-O0026", "side_space_id": P + "F0-S021"},
        space("F0-S030"),
    )
    assert sided["reachable"]
    point = route(
        {"kind": "point", "space_id": P + "F0-S016", "position": [6.0, 30.0]}, space("F0-S030")
    )
    assert_official(point, building, P + "F0-S016", P + "F0-S030")
    assert point["start"]["kind"] == "point"
    assert point["start"]["requested_position"] == [6.0, 30.0]


def test_tour_with_via_matches_official_via(route: Route, building: dict[str, Any]) -> None:
    a, b, c = P + "F0-S021", P + "F1-S010", P + "F0-S030"
    tour = route(space("F0-S021"), space("F0-S030"), via=[space("F1-S010")])
    expected = official(building, a, c, via=[b])
    assert (tour["length_m"], tour["space_path"]) == (expected.length_m, expected.space_path)
    assert len(tour["legs"]) == 2
    for leg, (x, y) in zip(tour["legs"], [(a, b), (b, c)], strict=True):
        single = official(building, x, y)
        assert (leg["cost_m"], leg["space_path"]) == (single.length_m, single.space_path)
    legs = tour["legs"]
    assert tour["element_path"] == legs[0]["element_path"] + legs[1]["element_path"]


def test_no_route_round_the_outside(route: Route) -> None:
    res = route(space("F0-S015"), space("F0-S021"))
    assert "outside" not in res["space_path"]


def test_unofficial_rules_are_opt_in_and_labelled(route: Route) -> None:
    default = route(space("F0-S011"), space("F0-S015"))
    assert default["rules"]["forklift"] == "off"
    office = route(space("F0-S021"), space("F0-S030"), unofficial={"forklift": True})
    assert office["status"] == "unreachable"
    assert "forklift" in office["unreachable_reason"]
    assert any("unofficial" in w for w in office["warnings"])


@pytest.mark.parametrize(
    ("request_", "code"),
    [
        ({"start": space("F0-S021"), "target": {"kind": "room", "id": "x"}}, "invalid_input"),
        ({"start": space("nope"), "target": space("F0-S030")}, "unknown_place"),
        ({"start": None, "target": space("F0-S030")}, "invalid_input"),
        (
            {"start": space("F0-S021"), "target": {"kind": "asset", "id": "missing"}},
            "unknown_place",
        ),
        ({"start": space("F0-S021"), "target": space("F0-S030"), "mode": "fast"}, "invalid_input"),
        (
            {"start": space("F0-S021"), "target": space("F0-S030"), "step_free": "yes"},
            "invalid_input",
        ),
        (
            {"start": space("F0-S021"), "target": space("F0-S030"), "assume_closed": ["nope"]},
            "unknown_place",
        ),
        (
            {"start": space("F0-S021"), "target": space("F0-S030"), "assume_closed": "x"},
            "invalid_input",
        ),
        ({"start": space("F0-S021"), "target": space("F0-S030"), "via": "x"}, "invalid_input"),
        (
            {
                "start": {"kind": "opening", "id": P + "F0-O0026", "side_space_id": P + "F0-S030"},
                "target": space("F0-S030"),
            },
            "invalid_input",
        ),
    ],
)
def test_invalid_requests_raise_structured_errors(
    nav: NavigationService, request_: dict[str, Any], code: str
) -> None:
    with pytest.raises(NavigationError) as caught:
        nav.route(BID, request_)
    assert caught.value.code == code


def test_unknown_building(nav: NavigationService) -> None:
    with pytest.raises(NavigationError) as caught:
        nav.route("nope", {"start": space("F0-S021"), "target": space("F0-S030")})
    assert caught.value.code == "unknown_building"


def test_route_does_not_mutate_the_building(nav: NavigationService) -> None:
    before = nav.building(BID)
    nav.route(BID, {"start": space("F0-S021"), "target": space("F1-S010"), "step_free": True})
    assert nav.building(BID) == before
    assert math.isfinite(len(before["spaces"]))

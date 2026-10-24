"""Precision routing: basic obstacle-checked route on the verified in-repo factory, options ported
from the prototype's test_precision_options.py, and a clean `unsupported` where it cannot run."""

from typing import Any

import pytest

from app.domain.building import BuildingFile
from app.navigation import NavigationService
from app.navigation.errors import NavigationError
from app.storage import InMemoryBuildingRepository
from tests.navigation.conftest import BID, DATASET, DATASETS_DIR, P, make_service, space

INSIDE_CNC = [-7.791, 8.193]  # centre of CNC mill H-A1-A, i.e. inside its footprint


def precise(nav: NavigationService, **request: Any) -> dict[str, Any]:
    return nav.route(BID, {"mode": "precision", **request})


def test_support_is_verified_with_original_svgs(nav: NavigationService) -> None:
    support = nav.state(BID)["support"]
    assert support == {"official": True, "precision": True, "precision_reason": None}


def test_basic_precision_route(nav: NavigationService) -> None:
    res = precise(nav, start=space("F0-S020"), target=space("F0-S021"))
    assert (res["mode"], res["status"], res["reachable"]) == ("precision", "ok", True)
    assert res["checked"] == "obstacle_checked" and res["fully_checked"] is True
    assert res["length_m"] > 0 and res["profile"] == "stationary"
    assert [t["element_id"] for t in res["transitions"]] == [P + "F0-O0025", P + "F0-O0026"]
    wps = res["waypoints"]
    assert (wps[0]["kind"], wps[-1]["kind"], wps[-1]["fraction"]) == ("start", "target", 1.0)
    for segment in res["path"]:
        assert {"floor_id", "space_id", "kind", "points"} <= set(segment)


def test_profile_none_ignores_furniture_and_bad_profile(nav: NavigationService) -> None:
    ends = {"start": space("F0-S020"), "target": space("F0-S021")}
    stationary = precise(nav, **ends)
    free = precise(nav, **ends, profile="none")
    assert free["status"] == "ok" and free["length_m"] <= stationary["length_m"]
    assert any("ignored" in w for w in free["warnings"])
    with pytest.raises(NavigationError) as caught:
        precise(nav, **ends, profile="everything")
    assert caught.value.code == "invalid_input"


def test_point_inside_machine_needs_controlled_snap(nav: NavigationService) -> None:
    start = {"kind": "point", "space_id": P + "F0-S001", "position": INSIDE_CNC}
    with pytest.raises(NavigationError) as caught:
        precise(nav, start=start, target=space("F0-S019"))
    assert caught.value.code == "blocked_endpoint"
    assert caught.value.details["diagnosis"]["category"]
    snapped = precise(nav, start=start, target=space("F0-S019"), snap_m=2.0)
    [snap] = snapped["snapped_endpoints"]
    assert snap["role"] == "start" and 1.0 < snap["distance_m"] <= 2.0
    assert any("moved" in w for w in snapped["warnings"])
    with pytest.raises(NavigationError):
        precise(nav, start=start, target=space("F0-S019"), snap_m=0.3)


def test_precision_multi_floor_is_schematic_vertical(nav: NavigationService) -> None:
    res = precise(nav, start=space("F0-S021"), target=space("F1-S010"))
    assert (res["status"], res["checked"]) == ("ok", "schematic_vertical")
    vertical = [t for t in res["transitions"] if t["kind"] in ("stairs", "lift")]
    assert vertical and all(t["schematic"] for t in vertical)


def test_precision_to_exit_door_ends_at_the_door(nav: NavigationService) -> None:
    exit_id = P + "F0-O0054"
    res = precise(nav, start=space("F0-S021"), target={"kind": "opening", "id": exit_id})
    assert res["status"] == "ok"
    door = next(o for o in nav.building(BID)["openings"] if o["id"] == exit_id)
    assert res["path"][-1]["points"][-1] == pytest.approx(door["position"])


def test_precision_rejects_via(nav: NavigationService) -> None:
    with pytest.raises(NavigationError) as caught:
        precise(nav, start=space("F0-S021"), target=space("F0-S030"), via=[space("F0-S016")])
    assert caught.value.code == "invalid_input"


def test_unsupported_without_original_svgs() -> None:
    nav = make_service(datasets_dir=None)
    assert nav.state(BID)["support"]["precision"] is False
    with pytest.raises(NavigationError) as caught:
        precise(nav, start=space("F0-S020"), target=space("F0-S021"))
    assert caught.value.code == "geometry_unsupported"
    assert caught.value.details["status"] == "unsupported"
    assert caught.value.details["diagnosis"]["category"] == "unsupported"
    # Official routing keeps working.
    assert nav.route(BID, {"start": space("F0-S020"), "target": space("F0-S021")})["reachable"]


def test_unsupported_for_unverified_source_family() -> None:
    data = BuildingFile.model_validate_json((DATASETS_DIR / DATASET / "building.json").read_bytes())
    data.building.source = "resplan"
    repository = InMemoryBuildingRepository()
    repository.save(DATASET, data)
    nav = NavigationService(repository, DATASETS_DIR)
    support = nav.state(BID)["support"]
    assert support["precision"] is False and support["precision_reason"]
    with pytest.raises(NavigationError) as caught:
        precise(nav, start=space("F0-S020"), target=space("F0-S021"))
    assert caught.value.details["status"] == "unsupported"

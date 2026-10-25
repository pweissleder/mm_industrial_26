"""Width-checked official routing (`min_door_width_m`) and tolerant door/entrance search."""

from typing import Any

import pytest

from app.navigation import NavigationService
from app.navigation.errors import NavigationError
from tests.navigation.conftest import BID, P, make_service, space

QUERY: dict[str, Any] = {"mode": "official", "start": space("F0-S001"), "target": space("F0-S005")}
DOOR = P + "F0-O0002"


def doors(route: dict[str, Any]) -> list[str]:
    return [t["element_id"] for t in route["transitions"] if t["kind"] == "door"]


@pytest.fixture
def narrowed() -> NavigationService:
    """The sample building with one door on the S001 -> S005 route narrowed to 0.8 m."""
    nav = make_service()
    entry = nav._entry(BID)
    entry.openings[DOOR]["width_m"] = 0.8
    return nav


def test_without_width_the_official_route_is_unchanged(narrowed: NavigationService) -> None:
    plain = narrowed.route(BID, QUERY)
    assert plain["length_m"] == pytest.approx(84.16, abs=0.01) and DOOR in doors(plain)
    assert "width_check" not in plain and "min_door_width_m" not in plain["constraints"]


def test_too_narrow_door_is_avoided_when_an_alternative_exists(
    narrowed: NavigationService,
) -> None:
    route = narrowed.route(BID, QUERY | {"min_door_width_m": 0.85})
    assert route["reachable"] and DOOR not in doors(route)
    assert route["length_m"] > 84.16
    check = route["width_check"]
    assert check["required_m"] == 0.85 and check["narrowest_door_m"] >= 0.85
    assert check["unknown_width_doors"] == [] and "corridors" in check["scope"]
    assert route["constraints"]["min_door_width_m"] == 0.85
    assert DOOR not in route["closed"]  # avoided for this request only, not closed


def test_no_wide_enough_route_names_the_narrow_door() -> None:
    nav = make_service()
    route = nav.route(
        BID,
        {
            "mode": "official",
            "start": space("F0-S001"),
            "target": space("F0-S023"),
            "min_door_width_m": 1.0,
        },
    )
    assert not route["reachable"]
    assert route["diagnosis"]["category"] == "too_narrow"
    assert route["diagnosis"]["elements"] == [{"id": P + "F0-O0028"}]
    assert "0.90 m door" in route["unreachable_reason"]


def test_step_free_still_binds_with_a_width(nav: NavigationService) -> None:
    route = nav.route(
        BID,
        {
            "mode": "official",
            "start": space("F0-S001"),
            "target": space("F1-S007"),
            "step_free": True,
            "min_door_width_m": 0.85,
        },
    )
    assert route["constraints"] == {"step_free": True, "min_door_width_m": 0.85}
    assert not any(t["kind"] == "stairs" for t in route["transitions"])


def test_width_is_rejected_for_precision_and_out_of_range(nav: NavigationService) -> None:
    with pytest.raises(NavigationError):
        nav.route(BID, QUERY | {"mode": "precision", "min_door_width_m": 1.0})
    with pytest.raises(NavigationError):
        nav.route(BID, QUERY | {"min_door_width_m": 9})


@pytest.mark.parametrize(
    "query",
    [
        "Entrance 18",
        "entrance18",
        "Entrance O0018",
        "door 18",
        "I'm at Entrance 18",
        "entrance 018",
    ],
)
def test_entrance_by_number(nav: NavigationService, query: str) -> None:
    [hit] = nav.search(BID, query, limit=5)
    assert hit["id"] == P + "F0-O0018" and hit["label"] == "Entrance O0018"


def test_position_phrases_find_rooms_and_equipment(nav: NavigationService) -> None:
    [room] = nav.search(BID, "Start at the quality lab", limit=3)
    assert room["id"] == P + "F0-S021"
    mills = nav.search(BID, "I'm standing next to the CNC mill", limit=5)
    assert mills and all(h["type"] == "cnc_mill" for h in mills)


def test_equipment_start_stands_beside_the_item(nav: NavigationService) -> None:
    query = {
        "mode": "official",
        "start": {"kind": "asset", "id": P + "A00001"},
        "target": space("F0-S028"),
    }
    plain = nav.route(BID, query)
    beside = nav.route(BID, query | {"beside_asset": True})
    asset = next(a for a in nav.building(BID)["assets"] if a["id"] == P + "A00001")
    fp = asset["footprint"]
    half_diag = 0.5 * (fp["width_m"] ** 2 + fp["depth_m"] ** 2) ** 0.5

    def off(point: list[float]) -> float:
        return float(
            ((point[0] - asset["position"][0]) ** 2 + (point[1] - asset["position"][1]) ** 2) ** 0.5
        )

    assert off(plain["start"]["position"]) < half_diag  # the official start is in the machine
    assert off(beside["start"]["position"]) > half_diag  # the person stands outside it
    assert beside["path"][0]["points"][0] == beside["start"]["position"]
    assert beside["start"]["kind"] == "asset" and beside["length_m"] == plain["length_m"]

"""Search (rooms, assets, exits) and nearest-by-official-walking-cost on the in-repo building."""

from typing import Any

import pytest

from app.navigation import NavigationService
from app.navigation.errors import NavigationError
from tests.navigation.conftest import BID, P, make_service, space

EXITS = {P + "F0-O0018", P + "F0-O0019", P + "F0-O0020", P + "F0-O0054"}


def test_search_room_by_name(nav: NavigationService) -> None:
    [hit] = nav.search(BID, "Quality lab", limit=3)
    assert (hit["kind"], hit["id"], hit["floor_level"]) == ("room", P + "F0-S021", 0)
    assert hit["name"] == "Quality lab 0.21" and hit["score"] > 0


def test_search_asset_with_kind_filter(nav: NavigationService) -> None:
    hits = nav.search(BID, "cnc", kind="asset", limit=3)
    assert len(hits) == 3
    assert all(h["kind"] == "asset" and h["type"] == "cnc_mill" for h in hits)
    assert all(h["space_id"] == P + "F0-S001" and h["space_name"] for h in hits)


def test_search_exits(nav: NavigationService) -> None:
    hits = nav.search(BID, "exit", limit=10)
    assert {h["id"] for h in hits} == EXITS
    assert all(h["kind"] == "opening" and h["is_exit"] is True for h in hits)
    by_kind = nav.search(BID, "exit", kind="exit", limit=10)
    assert {h["id"] for h in by_kind} == EXITS


def test_search_floor_filter(nav: NavigationService) -> None:
    hits = nav.search(BID, "office", kind="room", floor_level=1, limit=50)
    assert hits and all(h["floor_level"] == 1 for h in hits)


def test_nearest_exit_ranked_by_official_walking_cost(nav: NavigationService) -> None:
    result = nav.nearest_from(BID, space("F0-S021"), "exit", limit=10)
    rows = result["results"]
    assert {r["id"] for r in rows} == EXITS
    costs = [r["walk_cost_m"] for r in rows]
    assert costs == sorted(costs)
    assert rows[0]["id"] == P + "F0-O0054" and rows[0]["walk_cost_m"] == 60.3
    assert result["from"]["space_id"] == P + "F0-S021"
    assert "official" in result["cost_model"]
    # Each walking cost is exactly the official route length to that exit door.
    for row in rows:
        routed = nav.route(BID, {"start": space("F0-S021"), "target": row["endpoint"]})
        assert routed["length_m"] == pytest.approx(row["walk_cost_m"], abs=0.005 + 1e-9)
    assert [r["id"] for r in nav.nearest(BID, space("F0-S021"), "exit", limit=2)] == [
        r["id"] for r in rows[:2]
    ]


def test_nearest_room_and_asset_type(nav: NavigationService) -> None:
    toilets = nav.nearest(BID, space("F0-S021"), "room_type:toilets", limit=3)
    assert toilets and all(r["type"] == "toilet" for r in toilets)
    assert toilets[0]["id"] == P + "F0-S022"
    mills = nav.nearest(BID, space("F0-S021"), "asset_type:cnc_mill", limit=3)
    assert mills and all(r["kind"] == "asset" and r["type"] == "cnc_mill" for r in mills)
    costs = [r["walk_cost_m"] for r in mills]
    assert costs == sorted(costs)


def test_nearest_omits_unreachable_and_respects_step_free() -> None:
    nav = make_service()
    revision = nav.state(BID)["revision"]
    nav.change(BID, "close", P + "F0-O0054", revision)
    rows = nav.nearest(BID, space("F0-S021"), "exit", limit=10)
    assert {r["id"] for r in rows} == EXITS - {P + "F0-O0054"}
    nav.change(BID, "close", P + "V02", revision + 1)
    upstairs: list[dict[str, Any]] = nav.nearest(
        BID, space("F1-S010"), "exit", step_free=True, limit=10
    )
    assert upstairs == []  # the lift is closed and stairs are not step-free


@pytest.mark.parametrize("category", ["", "nearest", "room_type:", "exit:x", 3])
def test_nearest_invalid_category(nav: NavigationService, category: Any) -> None:
    with pytest.raises(NavigationError) as caught:
        nav.nearest(BID, space("F0-S021"), category)
    assert caught.value.code == "invalid_input"

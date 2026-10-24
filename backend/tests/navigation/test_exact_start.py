"""Opt-in `exact_start` (live guidance reroutes): the official route is unchanged (cost, spaces,
elements), only its first leg starts at the given point, walked on the official walkable area."""

import pytest

from app.navigation import NavigationService
from tests.navigation.test_qa_regressions import DEV, C, pytestmark, service  # noqa: F401

__all__ = ["pytestmark", "service"]


def test_exact_start_keeps_the_official_route_and_starts_at_the_point(
    service: NavigationService,  # noqa: F811
) -> None:
    request = {
        "mode": "official",
        "start": {"kind": "point", "space_id": f"{C}-F3-S001", "position": [19.86, 6.79]},
        "target": {"kind": "opening", "id": f"{C}-F0-O0023"},
    }
    base = service.route(C, request)
    exact = service.route(C, {**request, "exact_start": True})
    assert base["start"]["position"] != [19.86, 6.79]  # official: the point resolves to its space
    assert exact["start"]["position"] == [19.86, 6.79]
    assert exact["waypoints"][0]["position"] == [19.86, 6.79]
    for key in ("length_m", "space_path", "element_path"):
        assert exact[key] == base[key]
    assert exact["path"][0]["points"][-1] == base["path"][0]["points"][-1]  # same door
    assert exact["exact_start_m"] == pytest.approx(7.945, abs=0.01)


def test_exact_start_is_ignored_for_non_point_starts(service: NavigationService) -> None:  # noqa: F811
    request = {
        "mode": "official",
        "start": {"kind": "space", "id": f"{C}-F3-S001"},
        "target": {"kind": "opening", "id": f"{C}-F0-O0023"},
    }
    assert (
        service.route(C, {**request, "exact_start": True})["path"]
        == service.route(C, request)["path"]
    )


_ = DEV

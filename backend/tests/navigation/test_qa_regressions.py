"""Phase A QA findings N01-N03 on the real dev buildings (QA_REPORT.md, minimal_repros.json).

N01: a route that starts in a lift drew the ride at the end; N02: precision dropped consecutive
stair rides; N03: the arrival waypoint of a ride used the departure XY. Skipped when the dev
buildings are not available locally (SYNERA_DEV_DATA, default ~/Documents/HackathonTransfer/...).
"""

import os
from pathlib import Path
from typing import Any

import pytest

from app.domain.building import BuildingFile
from app.navigation import NavigationService
from app.storage import InMemoryBuildingRepository

DEV = Path(
    os.environ.get("SYNERA_DEV_DATA")
    or Path.home() / "Documents" / "HackathonTransfer" / "SyneraKit-data" / "dev"
)
C, OSM = "proc-campus-dev-001", "osm-way22744230"

pytestmark = pytest.mark.skipif(
    not all((DEV / b / "building.json").is_file() for b in (C, OSM)),
    reason=f"dev buildings not found under {DEV}",
)


@pytest.fixture(scope="module")
def service() -> NavigationService:
    repo = InMemoryBuildingRepository()
    for b in (C, OSM):
        text = (DEV / b / "building.json").read_text(encoding="utf-8")
        repo.save(b, BuildingFile.model_validate_json(text))
    return NavigationService(repo, DEV)


def floors(route: dict[str, Any]) -> list[str]:
    """Floors visited in path order, consecutive duplicates removed."""
    out: list[str] = []
    for seg in route["path"]:
        for floor in (seg["floor_id"], seg.get("to_floor_id")):
            if floor and (not out or out[-1] != floor):
                out.append(floor)
    return out


def test_n01_route_starting_in_a_lift_rides_first(service: NavigationService) -> None:
    route = service.route(
        C,
        {
            "mode": "official",
            "start": {"kind": "space", "id": f"{C}-F3-S011"},
            "target": {"kind": "asset", "id": f"{C}-A00414"},
        },
    )
    wp = route["waypoints"]
    assert wp[0]["floor_id"] == f"{C}-F3" and wp[0]["kind"] == "start"
    assert wp[-1]["floor_id"] == f"{C}-F2" and wp[-1]["position"] == pytest.approx([42.83, 11.4])
    assert route["path"][0]["kind"] == "connector"
    assert floors(route) == [f"{C}-F3", f"{C}-F2"]
    assert route["transitions"][0]["kind"] == "lift"


def test_n02_precision_keeps_every_stair_ride(service: NavigationService) -> None:
    route = service.route(
        C,
        {
            "mode": "precision",
            "profile": "none",
            "width_m": 0.3,
            "start": {"kind": "space", "id": f"{C}-F3-S001"},
            "target": {"kind": "opening", "id": f"{C}-F0-O0022"},
        },
    )
    rides = [
        (t["from_floor_id"], t["to_floor_id"]) for t in route["transitions"] if t["kind"] != "door"
    ]
    assert rides == [(f"{C}-F3", f"{C}-F2"), (f"{C}-F2", f"{C}-F1"), (f"{C}-F1", f"{C}-F0")]
    assert floors(route) == [f"{C}-F{i}" for i in (3, 2, 1, 0)]
    assert route["waypoints"][-1]["floor_id"] == f"{C}-F0"


def test_n03_ride_arrival_uses_the_landing_xy(service: NavigationService) -> None:
    route = service.route(
        OSM,
        {
            "mode": "official",
            "start": {"kind": "opening", "id": f"{OSM}-F4-O0285"},
            "target": {"kind": "asset", "id": f"{OSM}-A00817"},
        },
    )
    arrival = next(
        w
        for w in route["waypoints"]
        if w["floor_id"] == f"{OSM}-F6" and w.get("element_id") == f"{OSM}-V06"
    )
    assert arrival["position"] == pytest.approx([9.045689208269721, -9.1945])
    assert floors(route) == [f"{OSM}-F4", f"{OSM}-F5", f"{OSM}-F6"]

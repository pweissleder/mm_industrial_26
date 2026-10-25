"""S01/S02: relative sides at four view headings, visibility only inside the person's own room,
no sides without a heading (the assistant must ask)."""

from pathlib import Path

import pytest

from app.config import BACKEND_DIR
from app.domain.building import BuildingFile
from app.navigation import NavigationService
from app.storage import InMemoryBuildingRepository

B = "proc-factory-dev-003"
OFFICE = f"{B}-F1-S004"  # Office 1.04: door to the corridor on its east wall at (5.0, 2.718)


@pytest.fixture(scope="module")
def service() -> NavigationService:
    path = BACKEND_DIR / "data" / "datasets" / B / "building.json"
    repo = InMemoryBuildingRepository()
    repo.save(B, BuildingFile.model_validate_json(Path(path).read_text(encoding="utf-8")))
    return NavigationService(repo)


def door(result: dict) -> dict:
    return next(i for i in result["items"] if i["kind"] == "door")


@pytest.mark.parametrize(
    ("heading", "side"), [(0, "ahead"), (90, "right"), (180, "behind"), (270, "left")]
)
def test_door_side_follows_the_view_direction(
    service: NavigationService, heading: int, side: str
) -> None:
    result = service.surroundings(B, OFFICE, [2.5, 2.7], heading)
    assert door(result)["side"] == side
    assert door(result)["distance_m"] == pytest.approx(2.5, abs=0.1)


def test_things_in_other_rooms_are_never_visible(service: NavigationService) -> None:
    result = service.surroundings(B, OFFICE, [4.6, 2.7], 0)
    others = [i for i in result["items"] if i["kind"] == "thing" and "another room" in i["where"]]
    assert others, "expected nearby things beyond the office wall"
    assert all(i["visible"] is False for i in others)


def test_without_a_heading_no_sides_and_a_note(service: NavigationService) -> None:
    result = service.surroundings(B, OFFICE, [2.5, 2.7], None)
    assert result["heading_known"] is False and "ask" in result["note"]
    assert all("side" not in i for i in result["items"])


def test_doors_further_along_a_wall_carry_the_wall_side(service: NavigationService) -> None:
    # Office 1.04's door is on its east wall at (5.0, 2.718). Standing at the west side and
    # looking north (90), the door is ahead-right at a shallow angle: sector "ahead"/"right",
    # and if in the "ahead" sector it must say wall_side "right".
    result = service.surroundings(B, OFFICE, [1.0, 0.8], 90)
    d = door(result)
    assert d["side"] in ("ahead", "right")
    if d["side"] == "ahead":
        assert d["wall_side"] == "right"

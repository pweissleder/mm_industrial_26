"""Left/right turns at doors in the human `directions` (response.turn_directions); the router's
own lines stay unchanged in `directions_raw`."""

from collections.abc import Callable
from typing import Any

import pytest

from app.navigation.response import door_turn, turn_directions
from tests.navigation.conftest import space

Route = Callable[..., dict[str, Any]]


def wp(x: float, y: float, kind: str = "walk", floor: str = "F0") -> dict[str, Any]:
    return {"floor_id": floor, "position": [x, y], "kind": kind}


def corridor_then_door(door_x: float, door_y: float, inside: tuple[float, float]) -> list[Any]:
    """Walk north along x=0, then through a door 0.3 m stubs either side."""
    return [
        wp(0, 0, "start"),
        wp(0, 9.7),
        wp(door_x, door_y, "door"),
        wp(*inside),
        wp(inside[0] + (inside[0] - door_x) * 10, inside[1] + (inside[1] - door_y) * 10, "target"),
    ]


LINES = ["Start in A.", "Walk about 10 m through the door into Kitchen 0.28.", "Walk about 3 m."]


@pytest.mark.parametrize(
    ("door", "inside", "expected"),
    [
        ((-0.3, 9.7), (-0.6, 9.7), "Walk about 10 m, then turn left through the door into"),
        ((0.3, 9.7), (0.6, 9.7), "Walk about 10 m, then turn right through the door into"),
        ((0, 10.0), (0, 10.3), "Walk about 10 m through the door into"),
    ],
)
def test_turn_before_the_door(door: Any, inside: Any, expected: str) -> None:
    out = turn_directions(LINES, corridor_then_door(*door, inside))
    assert out[1] == expected + " Kitchen 0.28."
    assert out[0] == LINES[0] and out[2] == LINES[2]


def test_turn_behind_the_door_and_turn_around() -> None:
    # Straight through a door heading north, then the path goes west (left) behind it.
    points = [wp(0, 0, "start"), wp(0, 9.7), wp(0, 10, "door"), wp(0, 10.3), wp(-8, 10.3, "target")]
    out = turn_directions(LINES, points)
    assert out[1] == "Walk about 10 m through the door into Kitchen 0.28, then turn left."
    # Heading north, then back south through a door in the side wall.
    points = [wp(0, 0, "start"), wp(0, 9.7), wp(0.3, 9.7, "door"), wp(0.6, 9.7), wp(0.6, 0)]
    before, after = door_turn(points, 2) or (0.0, 0.0)
    assert before == pytest.approx(-90) and after == pytest.approx(-90)
    assert "turn around" in turn_directions(LINES, points)[1]


def test_lines_unchanged_when_doors_do_not_match_or_walk_is_too_short() -> None:
    points = corridor_then_door(-0.3, 9.7, (-0.6, 9.7))
    two_lines = [LINES[1], LINES[1]]
    assert turn_directions(two_lines, points) == two_lines  # 2 lines, 1 door
    short = [wp(0, 0, "start"), wp(-0.1, 0, "door"), wp(-0.1, 5, "target")]
    assert door_turn(short, 1) is None
    assert turn_directions(LINES, short) == LINES


def test_office_to_kitchen_real_route(route: Route) -> None:
    res = route(space("F0-S038"), space("F0-S028"))
    assert res["directions"] == [
        "Start in Office 0.38 on Ground floor.",
        "Walk about 2 m through the door into Office corridor 0.16, then turn right.",
        "Walk about 16 m, then turn left through the door into Kitchen 0.28.",
        "Walk about 2 m. You have arrived at Kitchen 0.28 on Ground floor.",
    ]
    # The router's own text is kept as it was.
    assert res["directions_raw"][1] == "Walk about 2 m through the door into Office corridor 0.16."
    assert res["directions_raw"][2] == "Walk about 16 m through the door into Kitchen 0.28."


def test_precision_route_gets_turns_too(route: Route) -> None:
    res = route(space("F0-S038"), space("F0-S028"), mode="precision")
    through = [line for line in res["directions"] if " through the door " in line]
    assert len(through) == 2
    assert any(" turn left " in line or " turn right" in line for line in through)

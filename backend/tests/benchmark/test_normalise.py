"""The deterministic route-answer normaliser."""

import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.benchmark.normalise import AnswerNormaliser, route_results
from app.domain.building import BuildingFile
from tests.pipeline.factories import connector, floor, make_building_dict, opening, space


@pytest.fixture
def normaliser() -> AnswerNormaliser:
    """Ground floor: S1 - S2 - S3 in a row, S1 next to lift stop L0. Floor 1: lift stop L1
    next to R1. Both lift stops are named "Lift 1", as in the kit."""
    building = make_building_dict(
        floors=[floor("F0"), floor("F1", level=1, elevation_m=3.0)],
        spaces=[
            space("S1", box=(0, 0, 10, 10)),
            space("S2", box=(10, 0, 20, 10)),
            space("S3", box=(20, 0, 30, 10)),
            space("L0", box=(0, 10, 3, 13), type="lift", name="Lift 1", ref=""),
            space("L1", "F1", box=(0, 10, 3, 13), type="lift", name="Lift 1", ref=""),
            space("R1", "F1", box=(0, 13, 10, 23)),
        ],
        openings=[
            opening("O12", ["S1", "S2"], (10, 5)),
            opening("O23", ["S2", "S3"], (20, 5)),
            opening("OL0", ["S1", "L0"], (1.5, 10)),
            opening("OL1", ["L1", "R1"], (1.5, 13), floor_id="F1"),
        ],
        vertical_connectors=[connector("V1", ["F0", "F1"], ["L0", "L1"], kind="lift")],
        assets=[],
    )
    return AnswerNormaliser(BuildingFile.model_validate(building))


def answer_and_structure(reply: str) -> tuple[str, dict[str, Any] | None]:
    lines = reply.splitlines()
    structures = [
        json.loads(line[len("Route JSON: ") :]) for line in lines if line.startswith("Route JSON: ")
    ]
    assert len(structures) <= 1
    return lines[-1], structures[0] if structures else None


def test_names_and_letter_case_become_exact_ids(normaliser: AnswerNormaliser) -> None:
    reply = normaliser.normalise("Walk east.\nAnswer: Room S1 -> s2 → `S3`", "route", [])
    assert reply.startswith("Walk east.\nRoute JSON: ")
    answer, structure = answer_and_structure(reply)
    assert answer == "Answer: S1 -> S2 -> S3"
    assert structure == {
        "from_space_id": "S1",
        "to_space_id": "S3",
        "reachable": True,
        "space_path": ["S1", "S2", "S3"],
        "element_path": ["O12", "O23"],
        "length_m": structure["length_m"] if structure else None,
    }
    assert structure is not None and 15 < structure["length_m"] < 25


def test_lift_stops_sharing_a_name_are_told_apart_by_connections(
    normaliser: AnswerNormaliser,
) -> None:
    reply = normaliser.normalise("Answer: Room S1 -> Lift 1 -> Lift 1 -> Room R1", "route", [])
    answer, structure = answer_and_structure(reply)
    assert answer == "Answer: S1 -> L0 -> L1 -> R1"
    assert structure is not None and structure["element_path"] == ["OL0", "V1", "OL1"]


def test_ids_after_a_stray_phrase_are_kept(normaliser: AnswerNormaliser) -> None:
    reply = normaliser.normalise("Answer: Room S1 -> Room S3 route: S1 -> S2 -> S3", "route", [])
    assert answer_and_structure(reply)[0] == "Answer: S1 -> S2 -> S3"


def test_destination_only_falls_back_to_the_agents_route(normaliser: AnswerNormaliser) -> None:
    tool = {
        "reachable": True,
        "spaces": ["S1", "S2", "S3"],
        "elements": ["O12", "O23"],
        "length_m": 19.9,
    }
    routes = [{"reachable": True, "spaces": ["S1", "S2"]}, tool]
    answer, structure = answer_and_structure(
        normaliser.normalise("Answer: Room S3", "route", routes)
    )
    assert answer == "Answer: S1 -> S2 -> S3"
    assert structure is not None and structure["length_m"] == 19.9  # the agent's own result
    assert normaliser.normalise("No line at all", "tour", routes).endswith("S1 -> S2 -> S3")
    unreachable = [{"reachable": False, "spaces": []}]
    reply = normaliser.normalise("Answer: Room S3", "route", unreachable)
    assert answer_and_structure(reply) == ("Answer: no route", {"reachable": False})


def test_no_route_answers_keep_their_line_and_get_a_structure(
    normaliser: AnswerNormaliser,
) -> None:
    reply = normaliser.normalise("Only stairs lead up.\nAnswer: no route", "route_step_free", [])
    assert answer_and_structure(reply) == ("Answer: no route", {"reachable": False})


def test_structure_is_written_once_on_a_second_pass(normaliser: AnswerNormaliser) -> None:
    once = normaliser.normalise("Answer: S1 -> S2 -> S3", "route", [])
    assert normaliser.normalise(once, "route", []) == once


def test_other_answers_are_left_alone(normaliser: AnswerNormaliser) -> None:
    routes = [{"reachable": True, "spaces": ["S1", "S2", "S3"]}]
    for reply, kind in [("Answer: 42.5", "errand"), ("Answer: Room S3, 100 m²", "largest_room")]:
        assert normaliser.normalise(reply, kind, routes) == reply
    unreadable = "Answer: Room S1 -> Room S3"  # not connected, and no route result to use
    assert normaliser.normalise(unreadable, "route", []) == unreadable


def test_route_results_come_from_route_tool_calls_only() -> None:
    messages = [
        HumanMessage("How do I get there?"),
        AIMessage(
            "",
            tool_calls=[
                {"id": "a", "name": "find", "args": {}},
                {"id": "b", "name": "route", "args": {}},
            ],
        ),
        ToolMessage(json.dumps({"results": []}), tool_call_id="a"),
        ToolMessage(json.dumps({"reachable": True, "spaces": ["S1", "S2"]}), tool_call_id="b"),
        ToolMessage("not json", tool_call_id="b"),
    ]
    assert route_results(messages) == [{"reachable": True, "spaces": ["S1", "S2"]}]

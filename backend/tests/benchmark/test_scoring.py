"""Scoring of benchmark replies: organizer route rules, warm-up answers, refreshed answers."""

import json
from typing import Any

import pytest

from app.benchmark.data import Question
from app.benchmark.scoring import Judge, refresh_expected
from app.config import BACKEND_DIR
from scripts.benchmark import geometry_drift
from tests.pipeline.factories import asset, make_building_dict, opening, space

SAMPLE = BACKEND_DIR / "data" / "datasets" / "proc-factory-dev-003" / "building.json"


def _ring() -> dict[str, Any]:
    """Four rooms round a block. S1 -> S2 -> S3 is short; S1 -> S4 -> S3 is a long way round
    (doors in far corners); the S1/S2 door has a step; S5 has no door at all."""
    stepped = opening("O12", ["S1", "S2"], (10, 5))
    stepped["step_free"] = False
    return make_building_dict(
        spaces=[
            space("S1", box=(0, 0, 10, 10)),
            space("S2", box=(10, 0, 20, 10)),
            space("S3", box=(10, 10, 20, 20)),
            space("S4", box=(0, 10, 10, 20)),
            space("S5", box=(30, 0, 40, 10)),
        ],
        openings=[
            stepped,
            opening("O23", ["S2", "S3"], (15, 10)),
            opening("O34", ["S3", "S4"], (10, 19)),
            opening("O41", ["S4", "S1"], (1, 10)),
        ],
        assets=[asset("A1", "S3", (15, 15))],
    )


@pytest.fixture
def judge() -> Judge:
    return Judge(_ring())


def route_question(kind: str = "route") -> Question:
    return Question(id="Q1", building_id="test-001", kind=kind, text="?", group="basic")


def route_answer(goal: str = "S3", **extra: Any) -> dict[str, Any]:
    return {"from_space_id": "S1", "to_space_id": goal, **extra}


def test_best_route_scores_full_marks(judge: Judge) -> None:
    score = judge.score(route_question(), route_answer(), "Go via S2.\nAnswer: S1 -> S2 -> S3")
    assert score is not None and score.correct and score.value == 1.0


def test_longer_valid_route_scores_best_over_ours(judge: Judge) -> None:
    score = judge.score(route_question(), route_answer(), "Answer: S1 -> S4 -> S3")
    assert score is not None and not score.correct
    assert 0.0 < score.value < 0.9


def test_route_through_a_wall_is_invalid(judge: Judge) -> None:
    score = judge.score(route_question(), route_answer(), "Answer: S1 -> S3")
    assert score is not None and score.value == 0.0 and "invalid" in score.detail


def test_route_must_start_and_end_where_asked(judge: Judge) -> None:
    score = judge.score(route_question(), route_answer(), "Answer: S2 -> S3")
    assert score is not None and score.value == 0.0


def test_step_free_question_rejects_the_stepped_door(judge: Judge) -> None:
    question = route_question("route_step_free")
    stepped = judge.score(question, route_answer(), "Answer: S1 -> S2 -> S3")
    assert stepped is not None and stepped.value == 0.0
    level = judge.score(question, route_answer(), "Answer: S1 -> S4 -> S3")
    assert level is not None and level.correct


def test_closed_door_must_be_avoided(judge: Judge) -> None:
    question = route_question("route_with_closure")
    answer = route_answer(closed_element_id="O23")
    blocked = judge.score(question, answer, "Answer: S1 -> S2 -> S3")
    assert blocked is not None and blocked.value == 0.0
    around = judge.score(question, answer, "Answer: S1 -> S4 -> S3")
    assert around is not None and around.correct


def test_no_route_is_the_right_answer_when_none_exists(judge: Judge) -> None:
    answer = route_answer("S5", reachable=False)
    right = judge.score(route_question(), answer, "S5 has no door.\nAnswer: no route")
    assert right is not None and right.correct
    wrong = judge.score(route_question(), route_answer(), "Answer: no route")
    assert wrong is not None and wrong.value == 0.0


def test_spaces_may_be_named(judge: Judge) -> None:
    score = judge.score(route_question(), route_answer(), "Answer: Room S1 → Room S2 → `S3`")
    assert score is not None and score.correct


def test_unknown_or_shortened_ids_are_unreadable(judge: Judge) -> None:
    score = judge.score(route_question(), route_answer(), "Answer: X-S1 -> X-S2 -> X-S3")
    assert score is not None and score.value == 0.0 and "could not read" in score.detail


def test_tour_visits_the_stops_in_order(judge: Judge) -> None:
    question = route_question("tour")
    answer = {"stops": ["S2", "S1", "S4"]}
    good = judge.score(question, answer, "Answer: S2 -> S1 -> S4")
    assert good is not None and good.correct
    skipped = judge.score(question, answer, "Answer: S2 -> S3 -> S4")
    assert skipped is not None and skipped.value == 0.0


def test_locate_and_count(judge: Judge) -> None:
    locate = Question(id="L", building_id="b", kind="locate", text="?", group="basic")
    found = {"space_id": "S3", "space_name": "Room S3"}
    hit = judge.score(locate, found, "Answer: Room S3 (Level 0)")
    miss = judge.score(locate, found, "Answer: Room S4")
    assert hit is not None and hit.correct and miss is not None and not miss.correct
    count = Question(id="C", building_id="b", kind="count", text="?", group="basic")
    assert judge.score(count, {"count": 3}, "There are 3.\nAnswer: 3") == judge.score(
        count, {"count": 3}, "Answer: 3"
    )
    two_numbers = judge.score(count, {"count": 3}, "Answer: 3 on floor 2")
    assert two_numbers is not None and not two_numbers.correct


def test_benchmark_questions_use_the_official_scorer(judge: Judge) -> None:
    question = Question(
        id="H", building_id="b", kind="floor_area_total", text="?", group="benchmark"
    )
    answer = {"type": "number", "value": 100.0, "relative_tolerance": 0.05, "absolute_tolerance": 0}
    good = judge.score(question, answer, "Answer: 103")
    assert good is not None and good.correct
    assert judge.score(question, answer, "Answer: between 95 and 105") == judge.score(
        question, answer, "no answer line"
    )
    assert judge.score(question, None, "Answer: 1") is None  # no answer key: unscored


def test_errand_answers_follow_the_kit_1_1_0_route_model() -> None:
    raw = json.loads(SAMPLE.read_text("utf-8"))
    inputs = {
        "start": "proc-factory-dev-003-F0-S038",
        "asset_type": "stove",
        "floor_id": "proc-factory-dev-003-F1",
        "room_type": "kitchen",
    }
    kit = {"type": "number", "value": 132.35, "relative_tolerance": 0.01, "absolute_tolerance": 1}
    question = Question("H04", "proc-factory-dev-003", "errand", "?", "benchmark", kit, inputs)

    refreshed, note = refresh_expected(raw, question)
    assert refreshed is not None and note is not None
    assert refreshed["value"] == pytest.approx(129.01, abs=0.01)
    assert refreshed["kit_value"] == 132.35

    current = Question(**{**question.__dict__, "answer": refreshed})
    assert refresh_expected(raw, current) == (refreshed, None)
    area = Question("H01", "b", "floor_area_total", "?", "benchmark", kit, inputs)
    assert refresh_expected(raw, area) == (kit, None)


def test_geometry_drift_reports_what_the_graders_would_see_change() -> None:
    raw = _ring()
    assert geometry_drift(raw, json.loads(json.dumps(raw))) == []
    moved = json.loads(json.dumps(raw))
    moved["openings"][1]["position"] = [15, 11]
    moved["assets"][0]["position"] = [12, 12]  # assets are not obstacles: not drift
    del moved["spaces"][4]
    assert geometry_drift(raw, moved) == ["openings/O23 changed", "spaces/S5 removed"]

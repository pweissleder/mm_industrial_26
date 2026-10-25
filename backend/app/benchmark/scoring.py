"""Scoring benchmark replies the way the judges do.

- Benchmark questions (`questions.jsonl`): the official scorer (vendored kit 1.1.0
  `score.py`, identical to the kit's `benchmark/score.py`).
- Warm-up routes: the organizers' route rules. Only the ordered list of spaces on the
  answer line counts; every step must go through a real door, opening, stair or lift and obey
  the rules (step-free, closures), and it is measured with the walking rule (door to door,
  15 cm off the walls; stairs 2 m per metre climbed; lifts 15 m + 1 m per metre).
  Score = best length / our length, full marks within 1 %, invalid = 0.
- Warm-up locate / count: the expected room, or exactly the expected number, on the
  answer line.

Kit 1.0.0 computed errand and what-if-door answers with the old route model (straight lines
through room anchors, 12 m per floor, free walks round the outside). Those expected values are
recomputed from the question's `inputs` with the official kit 1.1.0 code, so the score follows
the rules the Sunday benchmark is graded with. On kit 1.1.0 data nothing changes.
"""

import math
import re
from dataclasses import dataclass
from typing import Any

from app.benchmark.data import Question
from app.navigation.official import score as official
from app.navigation.ported import operations
from navgraph import scoring as route_rules
from navgraph.agent import BuildingIntelligence, ToolError
from navgraph.model import OUTSIDE, Building
from navgraph.rules import STEP_FREE, WALK

RULE_DEPENDENT_KINDS = {"errand", "what_if_door"}
ROUTE_KINDS = {"route", "route_step_free", "route_with_closure", "tour"}
FULL_MARKS_WITHIN = 0.01
NO_ROUTE = re.compile(
    r"\bno\s+(?:step-free\s+|valid\s+|possible\s+)?(?:route|path|way)\b|\bunreachable\b"
    r"|\bnot\s+(?:be\s+)?reachable\b|\bimpossible\b|\bcannot\s+be\s+reached\b",
    flags=re.IGNORECASE,
)
SEPARATORS = re.compile(r"\s*(?:->|→|=>|>|,|;|\|)\s*")
STRIP = " \t.`*'\"[]"


@dataclass(frozen=True)
class Score:
    correct: bool
    value: float  # 1.0 = full marks, 0.0 = wrong or invalid
    detail: str = ""


def refresh_expected(
    raw: dict[str, Any], question: Question
) -> tuple[dict[str, Any] | None, str | None]:
    """The expected answer under the kit 1.1.0 route model, and a note when it changed."""
    answer = question.answer
    if answer is None or question.kind not in RULE_DEPENDENT_KINDS or not question.inputs:
        return answer, None
    result = operations.compute(raw, question.kind, dict(question.inputs))  # type: ignore[no-untyped-call]
    value = float(result["value"])
    if math.isclose(value, float(answer["value"]), abs_tol=0.005):
        return answer, None
    note = f"expected {answer['value']} -> {value:.2f} (kit 1.1.0 route model)"
    return {**answer, "value": value, "kit_value": answer["value"]}, note


def _is_subsequence(needles: list[str], haystack: list[str]) -> bool:
    remaining = iter(haystack)
    return all(any(item == needle for item in remaining) for needle in needles)


class Judge:
    """Scores replies for one building, against its source data (what the graders use)."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        self.bi = BuildingIntelligence(Building.from_dict(raw))
        self.spaces = {s["id"]: s for s in raw["spaces"]}

    def score(self, question: Question, answer: dict[str, Any] | None, reply: str) -> Score | None:
        """None when the question has no answer key (test split, Sunday questions)."""
        if answer is None:
            return None
        if question.group == "benchmark":
            ok = official.score_reply(answer, reply)
            return Score(ok, float(ok))
        text = official.committed_answer(reply)
        if text is None:
            return Score(False, 0.0, "no 'Answer:' line")
        if question.kind == "locate":
            space = self.spaces.get(answer["space_id"], {})
            labels = [label for label in (space.get("ref"), space.get("name")) if label]
            ok = official.first_mention(text, labels) is not None
            return Score(ok, float(ok), "" if ok else f"expected {answer['space_name']}")
        if question.kind == "count":
            numbers = official.numbers_in(text)
            ok = numbers == [float(answer["count"])]
            return Score(ok, float(ok), "" if ok else f"expected {answer['count']}, got {numbers}")
        if question.kind in ROUTE_KINDS:
            return self.score_route(question.kind, answer, text)
        return None

    def parse_spaces(self, text: str) -> list[str] | None:
        ids = []
        for token in SEPARATORS.split(text):
            token = token.strip(STRIP)
            if not token:
                continue
            if token in self.bi.b.spaces or token == OUTSIDE:
                ids.append(token)
                continue
            try:
                ids.append(self.bi.resolve_space(token))
            except ToolError:
                return None
        return ids or None

    def score_route(self, kind: str, answer: dict[str, Any], text: str) -> Score:
        profile = STEP_FREE if kind == "route_step_free" else WALK
        if answer.get("closed_element_id"):
            profile = profile.with_closed(answer["closed_element_id"])
        stops = (
            list(answer["stops"])
            if kind == "tour"
            else [answer["from_space_id"], answer["to_space_id"]]
        )
        nav = self.bi.nav
        best = (
            nav.tour(stops, profile) if kind == "tour" else nav.route(stops[0], stops[1], profile)
        )
        kit_reachable = answer.get("reachable", True) is not False
        disagree = "" if best.reachable == kit_reachable else " (kit 1.0.0 answer disagrees)"
        says_no_route = bool(NO_ROUTE.search(text))
        if not best.reachable:
            note = "no route under the rules" + disagree
            return Score(says_no_route, float(says_no_route), note)
        if says_no_route:
            return Score(False, 0.0, "said there is no route, but one exists" + disagree)
        ours = self.parse_spaces(text)
        if ours is None:
            return Score(False, 0.0, "could not read the list of spaces")
        if ours[0] != stops[0] or ours[-1] != stops[-1] or not _is_subsequence(stops, ours):
            return Score(False, 0.0, "route does not start, stop and end where asked")
        problems = route_rules.validate(self.bi.b, ours, profile)
        if problems:
            return Score(False, 0.0, f"invalid route: {problems[0]}")
        mine = route_rules.measure(nav, ours, profile)
        optimal = route_rules.measure(nav, best.spaces, profile)
        value = 1.0 if mine <= optimal * (1 + FULL_MARKS_WITHIN) else optimal / mine
        return Score(value == 1.0, value, f"{mine:.1f} m vs best {optimal:.1f} m{disagree}")

"""OFFLINE tests of the dialog-matrix harness (scripts/dialog_sim.py): the deterministic checks on
the in-repo factory building, budget/error/skip handling, and scripted end-to-end dialogs through
the real Assistant + NavigationService (Claude replaced by the Scripted stand-in; no network)."""

from pathlib import Path
from typing import Any

import pytest

from app.assistant.service import Assistant, AssistantError
from app.config import BACKEND_DIR
from app.navigation import NavigationService
from scripts import dialog_sim as sim
from scripts.dialog_scenarios import SCENARIOS
from tests.assistant.test_assistant_unit import Scripted, update

B = "proc-factory-dev-003"
DATASETS = [BACKEND_DIR / "data" / "datasets"]
SCENARIO = {s["id"]: s for s in SCENARIOS}


def space(suffix: str) -> dict[str, str]:
    return {"kind": "space", "id": f"{B}-{suffix}"}


def find(query: str, kind: str | None = "room") -> tuple[str, dict[str, Any]]:
    return ("find_places", {"query": query, "kind": kind, "floor_level": None})


def route(destination: dict[str, str] | None, start: dict[str, str] | None = None) -> Any:
    args = {"start": start, "destination": destination, "mode": None, "step_free": None}
    return ("plan_route", args | {"assume_closed": []})


@pytest.fixture
def navigation() -> NavigationService:
    service, sources = sim.load_buildings([B], DATASETS)
    assert set(sources) == {B}
    return service


def verdicts(
    navigation: NavigationService,
    expect: dict[str, Any],
    out: dict[str, Any],
    state: dict[str, Any] | None = None,
) -> tuple[bool, dict[str, Any]]:
    return sim.check(
        expect,
        {"reply": "", "tools": [], "ui": {}} | out,
        navigation=navigation,
        building_id=B,
        state=state or {"start": None, "step_free": False, "mode": "official", "width_m": 0.6},
        closed_before=set(),
    )


# ------------------------------------------------------------------ check functions


def test_scenario_ids_exist_in_the_in_repo_factory(navigation: NavigationService) -> None:
    data = navigation.building(B)
    ids = {x["id"] for k in ("floors", "spaces", "assets", "openings") for x in data[k]}
    referenced = set()
    for scenario in SCENARIOS:
        if scenario["building"] != B:
            continue
        for turn in scenario["turns"]:
            for value in turn["expect"].values():
                values = value if isinstance(value, list) else [value]
                referenced |= {v for v in values if isinstance(v, str) and v.startswith(B)}
    assert referenced and referenced <= ids


def test_reply_text_checks(navigation: NavigationService) -> None:
    expect = {"short": True, "asks": True, "mentions_any": ["toilet"]}
    ok, v = verdicts(navigation, expect, {"reply": "Toilet 0.22 is close. Where are you now?"})
    assert ok and v["no_ids_in_reply"] and v["no_error"]
    long_reply = "One. Two. Three. Four."
    ok, v = verdicts(navigation, {"short": True, "asks": True}, {"reply": long_reply})
    assert not ok and v["short"] is False and v["asks"] is False
    _, v = verdicts(navigation, {"asks": True}, {"reply": "Please tell me which one you need."})
    assert v["asks"] is True
    _, v = verdicts(navigation, {}, {"reply": f"Go to {B}-F0-S022."})
    assert v["no_ids_in_reply"] is False
    _, v = verdicts(navigation, {}, {"reply": "The mill A00002 is there."})
    assert v["no_ids_in_reply"] is False


def test_honest_missing_needs_negation_and_no_route(navigation: NavigationService) -> None:
    expect = {"honest_missing": True, "no_route": True}
    ok, _ = verdicts(navigation, expect, {"reply": "I can't find a gym here."})
    assert ok
    ok, v = verdicts(navigation, expect, {"reply": "The gym is upstairs."})
    assert not ok and v["honest_missing"] is False
    ok, v = verdicts(navigation, expect, {"reply": "No gym.", "ui": {"route": {"status": "ok"}}})
    assert not ok and v["no_route"] is False


def test_tools_find_nearest_with_route_counts_as_plan_route() -> None:
    nearest = {"tool": "find_nearest", "input": {"category": "x", "and_route": True}}
    assert sim.tool_used([nearest], "plan_route")
    assert sim.tool_used([nearest], "find_nearest")
    assert not sim.tool_used(
        [{"tool": "find_nearest", "input": {"and_route": False}}], "plan_route"
    )
    assert not sim.tool_used([{"tool": "find_places", "input": {}}], "plan_route")


def test_route_endpoint_checks_accept_asset_or_its_room(navigation: NavigationService) -> None:
    request = {"start": space("F0-S019"), "target": {"kind": "asset", "id": f"{B}-A00002"}}
    out = {"ui": {"route_request": request, "route": {"status": "ok", "reachable": True}}}
    expect = {"route_from": f"{B}-F0-S019", "route_to": f"{B}-A00002", "route_found": True}
    ok, _ = verdicts(navigation, expect, out)
    assert ok
    # A failed (precision) route still carries its request in route_error.
    failed = {"ui": {"route_error": {"request": request}}}
    ok, v = verdicts(navigation, {"route_to": f"{B}-A00002", "route_found": True}, failed)
    assert v["route_to"] is True and v["route_found"] is False and not ok


def test_nearest_uses_official_walking_cost(navigation: NavigationService) -> None:
    start = space("F0-S038")
    want, cost = sim.nearest_spaces(navigation, B, start, "coffee", False)
    costs = navigation.walk_costs(B, start)
    data = navigation.building(B)
    targets = {s["id"] for s in data["spaces"] if s.get("room_type") in {"kitchen", "canteen"}}
    targets |= {a["space_id"] for a in data["assets"] if a.get("type") == "coffee_machine"}
    best = min(costs[t] for t in targets if t in costs)
    assert cost == best and want == {t for t in targets if costs.get(t) == best}
    assert want == {f"{B}-F0-S028"}  # Kitchen 0.28, as in the prototype matrix
    assert sim.nearest_spaces(navigation, B, None, "coffee", False) == (set(), None)

    state = {"start": start, "step_free": False}
    expect = {"mentions_nearest": "coffee"}
    ok, v = verdicts(navigation, expect, {"reply": "Kitchen 0.28 is close."}, state)
    assert ok and v["mentions_nearest_expected"] == ["Kitchen 0.28"]
    ok, _ = verdicts(navigation, expect, {"reply": "Try Kitchen 1.07."}, state)
    assert not ok
    out = {"ui": {"route_request": {"start": start, "target": space("F0-S028")}}}
    ok, _ = verdicts(navigation, {"route_to_nearest": "coffee"}, out, state)
    assert ok


def test_closure_checks_read_the_navigation_scenario(navigation: NavigationService) -> None:
    corridor = f"{B}-F0-S016"
    tools = [{"tool": "preview_closure", "input": {"element_ids": [corridor]}}]
    expect = {"previewed_closed": corridor, "unchanged": True, "open_now": corridor}
    ok, _ = verdicts(navigation, expect, {"tools": tools})
    assert ok
    navigation.change(B, "close", corridor, navigation.state(B)["revision"])
    ok, v = verdicts(navigation, {"closed_now": corridor, "unchanged": True}, {})
    assert v["closed_now"] is True and v["unchanged"] is False and not ok
    unreachable = {
        "tools": [{"tool": "plan_route", "input": {}}],
        "ui": {"route": {"status": "unreachable", "reachable": False}},
    }
    ok, _ = verdicts(navigation, {"route_unreachable": True}, unreachable)
    assert ok
    ok, _ = verdicts(navigation, {"route_unreachable": True}, {})  # no route attempt at all
    assert not ok


# ------------------------------------------------------------------ runner


class Costly:
    """Fixed reply that reports 100k input tokens (0.20 USD with the default model)."""

    def __init__(self) -> None:
        self.calls = 0

    def create(self, **_kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        return {
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 100_000},
            "content": [{"type": "text", "text": "Hello."}],
        }


class Failing:
    def __init__(self) -> None:
        self.calls = 0

    def create(self, **_kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        raise AssistantError("Claude API error (HTTP 500): boom No retry was made.")


def run(navigation: NavigationService, transport: Any, ids: list[str], **kw: Any) -> Any:
    assistant = Assistant(navigation, transport)
    scenarios = [SCENARIO[i] for i in ids]
    return sim.run(
        scenarios,
        assistant,
        navigation,
        available={B},
        max_usd=kw.pop("max_usd", 1.0),
        log=lambda _line: None,
        **kw,
    )


def test_budget_stops_before_exceeding_and_reports_skips(navigation: NavigationService) -> None:
    transport = Costly()
    results, totals = run(navigation, transport, ["coffee_nearest", "stt_errors"], max_usd=0.3)
    assert transport.calls == 1 and totals["estimated_usd"] == pytest.approx(0.2)
    assert [r["result"] for r in results] == ["partial", "skipped"]
    assert totals["skipped"] == [
        {"scenario": "coffee_nearest", "turn": 1, "reason": "budget"},
        {"scenario": "stt_errors", "turn": 0, "reason": "budget"},
    ]


def test_api_error_is_a_failed_turn_without_retry(navigation: NavigationService) -> None:
    transport = Failing()
    results, _ = run(navigation, transport, ["coffee_nearest"])
    turns = results[0]["turns"]
    assert transport.calls == 2  # one call per turn, never retried
    assert all(not t["passed"] and t["verdicts"]["no_error"] is False for t in turns)
    assert "HTTP 500" in turns[0]["error"]


def test_missing_building_is_skipped(navigation: NavigationService) -> None:
    results, totals = run(navigation, sim.FixedReply(), ["long_campus"])
    assert results[0]["result"] == "skipped"
    assert totals["skipped"][0]["reason"] == "building_missing"


COFFEE_PLAN = [
    find("office 0.38"),
    update(start=space("F0-S038")),
    (
        "find_nearest",
        {"category": "asset_type:coffee_machine", "step_free": None, "and_route": False},
    ),
    "The closest coffee machine is in Kitchen 0.28, a short walk from your office.",
    route(space("F0-S028")),
    "Head out into the office corridor; Kitchen 0.28 is a short walk away.",
]
CLOSURE_PLAN = [
    find("meeting room 0.20"),
    update(start=space("F0-S020")),
    find("office corridor 0.16"),
    ("preview_closure", {"element_ids": [f"{B}-F0-S016"]}),
    "Then the quality lab could not be reached from here. Nothing has been changed.",
    ("propose_change", {"action": "close", "element_id": f"{B}-F0-S016"}),
    "I have prepared closing Office corridor 0.16; please press Confirm.",
    find("quality lab"),
    route(space("F0-S021")),
    "Right now there is no way to the quality lab, because the corridor is closed.",
    ("propose_change", {"action": "open", "element_id": f"{B}-F0-S016"}),
    "Press Confirm to reopen Office corridor 0.16.",
    route(space("F0-S021")),
    "Now the quality lab is reachable through the corridor again.",
]


@pytest.mark.parametrize(
    ("scenario", "plan"), [("coffee_nearest", COFFEE_PLAN), ("closure_flow", CLOSURE_PLAN)]
)
def test_scripted_dialog_passes_end_to_end(
    navigation: NavigationService, scenario: str, plan: list[Any]
) -> None:
    transport = Scripted(plan)
    results, totals = run(navigation, transport, [scenario])
    record = results[0]
    failed = {
        t["index"]: [k for k, v in t["verdicts"].items() if v is False] for t in record["turns"]
    }
    assert record["result"] == "pass", failed
    assert transport.script == [] and totals["skipped"] == []
    turn = record["turns"][0]
    assert turn["input"]["ui"] == {"building_id": B} and turn["state"]["start"]
    if scenario == "closure_flow":
        confirms = [t.get("confirmation", {}).get("status") for t in record["turns"]]
        assert confirms == [None, "applied", None, "applied", None]
        assert record["turns"][2]["ui"]["route"]["status"] == "unreachable"
        assert navigation.state(B)["closed"] == []


def test_cli_scripted_writes_reports_and_live_needs_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    datasets = [str(DATASETS[0])]
    args = ["--scripted", "--only", "stt_errors,long_campus", "--label", "t", "--out-dir"]
    assert sim.main([*args, str(tmp_path), "--datasets-dir", *datasets]) == 0
    md = (tmp_path / "dialog_sim_t.md").read_text(encoding="utf-8")
    assert "| stt_errors | 1 |" in md and "SKIPPED (building_missing)" in md
    assert "Median Claude latency" in md and "Total estimated cost" in md
    assert (tmp_path / "dialog_sim_t.json").is_file()

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert sim.main(["--live", "--out-dir", str(tmp_path)]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err

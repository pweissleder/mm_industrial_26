"""OFFLINE dialog / state / tool scenarios (night task: 40-60 defined scenarios, mostly offline).

WHAT THIS TESTS - AND WHAT NOT: Claude is replaced by a scripted client that emits, turn by turn,
exactly the tool calls a model would make (tool_use blocks in sequence, then a final text). Every
scenario then checks what the REAL layers do with them: the Assistant's session state and UI sync
(start/destination/start_source/pose/inspected/settings), the tool handlers, the step-free / width
guards, closures and proposals, on the real NavigationService and real buildings. This is NOT a
language-understanding test: whether Claude picks these tool calls for a given sentence is covered
separately by a few live dialogs. A constant-text mock would test nothing here, so none is used.

Each scenario: turns of (UI payload, scripted tool calls, expectations on tool results, session
state, model-visible session_state, the UI/route payload and what must NOT have changed). Where the
current code is genuinely wrong the scenario stays strict and is marked xfail(strict=True) with the
defect as reason (docs/night/tests/dialog_scenarios_offline.md lists them).
"""

from __future__ import annotations

import copy
import functools
import json
import math
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.assistant.port import NavigationError
from app.assistant.service import Assistant
from app.config import BACKEND_DIR
from app.domain.building import BuildingFile
from app.memory.store import MemoryStore, SessionNotFoundError, compute_recap
from app.navigation import NavigationService
from app.storage import InMemoryBuildingRepository
from tests.assistant.test_assistant_unit import Scripted, update

JSON = dict[str, Any]
B = "proc-factory-dev-003"  # in-repo factory: F0 halls/offices, F1 offices; one lift, one stairs
B2 = "proc-campus-dev-000"  # dev campus (5 floors, 3 lifts), for building switch / vertical
DEV = Path(
    os.environ.get("SYNERA_DEV_DATA")
    or Path.home() / "Documents" / "HackathonTransfer" / "SyneraKit-data" / "dev"
)
DATASET_DIRS = [BACKEND_DIR / "data" / "datasets", DEV]
SID = "scenario-0001"


def sp(suffix: str, building: str = B) -> JSON:
    return {"kind": "space", "id": f"{building}-{suffix}"}


OFFICE = sp("F1-S004")  # Office 1.04 (F1), 5 x 5.43 m, door to the corridor at [5.0, 2.718]
OFFICE_106 = sp("F1-S006")
KITCHEN_107, KITCHEN_108 = sp("F1-S007"), sp("F1-S008")
TOILET_F1 = sp("F1-S032")
HALL_A1 = sp("F0-S001")  # Production zone H-A1 (ground floor)
CORRIDOR_F0 = sp("F0-S016")  # Office corridor 0.16
QUALITY_LAB = sp("F0-S021")
KITCHEN_028 = sp("F0-S028")
LIFT, STAIRS = f"{B}-V02", f"{B}-V01"
EXIT_ID = f"{B}-F0-O0054"  # Exit from Office corridor 0.16
EXIT_DOOR = {"kind": "opening", "id": EXIT_ID}
EXIT_XY = [6.0, 69.8]
CNC = {"kind": "asset", "id": f"{B}-A00001"}
POSE = {"floor_id": f"{B}-F1", "space_id": OFFICE["id"], "position": [2.5, 2.7]}
STEP_FREE_NOTE = "Never suggest stairs"


# ------------------------------------------------------------------ scripted model calls


def route(
    destination: JSON | None = None,
    start: JSON | None = None,
    step_free: bool | None = None,
    closed: list[str] | None = None,
    mode: str | None = None,
) -> tuple[str, JSON]:
    args = {"start": start, "destination": destination, "mode": mode, "step_free": step_free}
    return ("plan_route", args | {"assume_closed": closed or []})


def nearest(category: str, step_free: bool | None = None, and_route: bool = False) -> Any:
    return ("find_nearest", {"category": category, "step_free": step_free, "and_route": and_route})


def find(query: str, kind: str | None = None) -> tuple[str, JSON]:
    return ("find_places", {"query": query, "kind": kind, "floor_level": None})


def around(direction: str = "all") -> tuple[str, JSON]:
    return ("describe_surroundings", {"direction": direction})


def fact(operation: str, raw: str | None = None, **inputs: Any) -> tuple[str, JSON]:
    return ("building_fact", {"operation": operation, "arguments_json": raw or json.dumps(inputs)})


def propose(action: str, element: str) -> tuple[str, JSON]:
    return ("propose_change", {"action": action, "element_id": element})


def preview(*ids: str) -> tuple[str, JSON]:
    return ("preview_closure", {"element_ids": list(ids)})


def select(building_id: str) -> tuple[str, JSON]:
    return ("select_building", {"building_id": building_id})


# ------------------------------------------------------------------ harness


class RecordingNav:
    """The real NavigationService behind a recorder (what the assistant asked for, in order)."""

    def __init__(self, inner: NavigationService) -> None:
        self.inner = inner
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self.inner, name)
        if not callable(attr):
            return attr

        def recorded(*args: Any) -> Any:
            self.calls.append((name, copy.deepcopy(args)))
            return attr(*args)

        return recorded


class RouterIgnoresStepFree(RecordingNav):
    """Fault injection: a router that silently drops step_free (to exercise the assistant's own
    defence-in-depth guard; the real router honours step_free)."""

    injected_stairs = False

    def route(self, building_id: str, request: JSON) -> JSON:
        self.calls.append(("route", (building_id, copy.deepcopy(request))))
        result = self.inner.route(building_id, request | {"step_free": False})
        if any(t.get("kind") == "stairs" for t in result.get("transitions", [])):
            RouterIgnoresStepFree.injected_stairs = True
        return result


@dataclass
class Ctx:
    assistant: Assistant
    nav: RecordingNav
    inner: NavigationService
    building: str
    tmp: Path
    sid: str = SID
    transport: Scripted | None = None
    outs: list[JSON] = field(default_factory=list)

    def state(self, sid: str | None = None) -> JSON:
        return self.assistant.session(sid or self.sid).state

    def closed(self, building: str = B) -> list[str]:
        return list(self.inner.state(building)["closed"])

    def close(self, element: str, building: str = B) -> None:
        state = self.inner.state(building)
        self.inner.change(building, "close", element, state["revision"])


Check = Callable[[Ctx, JSON, JSON], None]  # (context, turn output, session state before turn)


@dataclass
class Turn:
    script: list[Any]
    ui: JSON = field(default_factory=dict)
    expect: list[Check] = field(default_factory=list)
    text: str = "(scripted user turn)"
    sid: str = SID
    raises: str | None = None  # NavigationError code the turn itself must raise
    before: Callable[[Ctx], None] | None = None


@dataclass
class Scenario:
    id: str
    row: str  # ACCEPTANCE_MATRIX row (or INV-n = NIGHT_TASK invariant n)
    checks: str  # what is checked (for the report)
    turns: list[Turn] = field(default_factory=list)
    building: str = B
    needs: tuple[str, ...] = (B,)
    setup: Callable[[Ctx], None] | None = None
    custom: Callable[[Ctx], None] | None = None
    nav: type[RecordingNav] = RecordingNav
    xfail: str | None = None


MISSING = object()


def dig(obj: Any, path: str) -> Any:
    for part in [p for p in path.split(".") if p]:
        if isinstance(obj, list):
            index = int(part)
            obj = obj[index] if -len(obj) <= index < len(obj) else MISSING
        elif isinstance(obj, dict):
            obj = obj.get(part, MISSING)
        else:
            return MISSING
    return obj


def requests(ctx: Ctx) -> list[JSON]:
    """The Messages API requests the scripted model received this turn."""
    assert ctx.transport is not None
    return list(ctx.transport.requests)


def seen_state(transport: Scripted | None) -> JSON:
    """The <session_state> JSON the model saw at the start of the turn."""
    assert transport is not None
    text = transport.requests[0]["messages"][-1]["content"]
    start = text.index("<session_state>") + len("<session_state>")
    return dict(json.loads(text[start : text.index("</session_state>")]))


# ------------------------------------------------------------------ checks


def labelled(label: str, fn: Check) -> Check:
    fn.__name__ = label
    return fn


def tool(index: int, name: str, status: str = "ok", error: str | None = None, **out: Any) -> Check:
    def check(ctx: Ctx, result: JSON, before: JSON) -> None:
        record = result["tools"][index]
        assert record["tool"] == name, record
        assert record["status"] == status, record["output"]
        if error is not None:
            assert record["output"]["error"] == error, record["output"]
        for path, value in out.items():
            got = dig(record["output"], path.replace("__", "."))
            assert got == value, f"{path}: {got!r} != {value!r}"

    return labelled(f"tool[{index}] {name} {status} {error or ''}", check)


def tool_out(index: int, path: str, predicate: Callable[[Any], bool], label: str) -> Check:
    def check(ctx: Ctx, result: JSON, before: JSON) -> None:
        got = dig(result["tools"][index]["output"], path)
        assert predicate(got), f"{label}: {path} = {got!r}"

    return labelled(f"tool[{index}].{path} {label}", check)


def tools(*names: str) -> Check:
    def check(ctx: Ctx, result: JSON, before: JSON) -> None:
        assert [t["tool"] for t in result["tools"]] == list(names)

    return labelled(f"tools == {names}", check)


def st(path: str, value: Any) -> Check:
    def check(ctx: Ctx, result: JSON, before: JSON) -> None:
        got = dig(ctx.state(ctx.sid), path)
        assert got == value, f"state.{path}: {got!r} != {value!r}"

    return labelled(f"state.{path} == {value!r}", check)


def same(*keys: str) -> Check:
    def check(ctx: Ctx, result: JSON, before: JSON) -> None:
        now = ctx.state(ctx.sid)
        for key in keys:
            assert now.get(key) == before.get(key), (
                f"{key} changed: {before.get(key)!r} -> {now.get(key)!r}"
            )

    return labelled(f"unchanged {keys}", check)


def snap(path: str, value: Any) -> Check:
    def check(ctx: Ctx, result: JSON, before: JSON) -> None:
        got = dig(result["state"], path)
        assert got == value, f"snapshot.{path}: {got!r} != {value!r}"

    return labelled(f"snapshot.{path} == {value!r}", check)


def seen(path: str, value: Any) -> Check:
    def check(ctx: Ctx, result: JSON, before: JSON) -> None:
        assert ctx.transport is not None
        got = dig(seen_state(ctx.transport), path)
        assert got == value, f"model saw {path}: {got!r} != {value!r}"

    return labelled(f"model sees {path} == {value!r}", check)


def context_has(text: str) -> Check:
    def check(ctx: Ctx, result: JSON, before: JSON) -> None:
        assert ctx.transport is not None
        assert text in json.dumps(ctx.transport.requests[0]["messages"], ensure_ascii=False)

    return labelled(f"model context contains {text!r}", check)


def ui(path: str, value: Any) -> Check:
    def check(ctx: Ctx, result: JSON, before: JSON) -> None:
        got = dig(result["ui"], path)
        assert got == value, f"ui.{path}: {got!r} != {value!r}"

    return labelled(f"ui.{path} == {value!r}", check)


def ui_lacks(*keys: str) -> Check:
    def check(ctx: Ctx, result: JSON, before: JSON) -> None:
        present = [k for k in keys if result["ui"].get(k) is not None]
        assert not present, f"unexpected ui keys {present}"

    return labelled(f"ui lacks {keys}", check)


def no_stairs() -> Check:
    """No stairs anywhere: shown route, route summaries, route_to_closest, element path."""

    def check(ctx: Ctx, result: JSON, before: JSON) -> None:
        route_ = result["ui"].get("route")
        if route_:
            assert not [t for t in route_["transitions"] if t["kind"] == "stairs"]
            assert STAIRS not in route_.get("element_path", [])
        for record in result["tools"]:
            out = record["output"]
            for summary in (out, out.get("route_to_closest") or {}):
                changes = summary.get("floor_changes") or []
                assert not [c for c in changes if c.get("kind") == "stairs"], summary

    return labelled("no stairs in any route", check)


def vertical(*kinds: str) -> Check:
    def check(ctx: Ctx, result: JSON, before: JSON) -> None:
        route_ = result["ui"]["route"]
        got = [t["kind"] for t in route_["transitions"] if t["kind"] in {"stairs", "lift"}]
        assert got == list(kinds), got

    return labelled(f"vertical transitions == {kinds}", check)


def called(name: str, times: int | None = None, **arg_checks: Any) -> Check:
    """NavigationService calls this turn; arg_checks: positional index (`a3`) or request key."""

    def check(ctx: Ctx, result: JSON, before: JSON) -> None:
        calls = [args for n, args in ctx.nav.calls if n == name]
        if times is not None:
            assert len(calls) == times, f"{name} called {len(calls)}x, expected {times}"
        assert calls, f"{name} never called"
        for key, value in arg_checks.items():
            for args in calls:
                got = args[int(key[1:])] if key.startswith("a") else args[1].get(key)
                assert got == value, f"{name} {key}: {got!r} != {value!r}"

    return labelled(f"nav.{name} called {times or ''} {arg_checks or ''}", check)


def not_called(*names: str) -> Check:
    def check(ctx: Ctx, result: JSON, before: JSON) -> None:
        hit = [n for n, _ in ctx.nav.calls if n in names]
        assert not hit, f"unexpected navigation calls {hit}"

    return labelled(f"nav not called {names}", check)


def closed_now(*ids: str) -> Check:
    def check(ctx: Ctx, result: JSON, before: JSON) -> None:
        assert ctx.closed() == sorted(ids)

    return labelled(f"scenario closed == {ids}", check)


def custom(label: str, fn: Callable[[Ctx, JSON], Any]) -> Check:
    def check(ctx: Ctx, result: JSON, before: JSON) -> None:
        assert fn(ctx, result), label

    return labelled(label, check)


def door_point(route_: JSON) -> list[float]:
    return list(route_["waypoints"][-1]["position"])


# ------------------------------------------------------------------ out-of-dialog actions


def confirm_last(approved: bool, expect_status: str) -> Callable[[Ctx], None]:
    def act(ctx: Ctx) -> None:
        proposal = ctx.outs[-1]["ui"]["proposal"]["proposal_id"]
        result = ctx.assistant.confirm(ctx.sid, proposal, approved)
        assert result["status"] == expect_status
        if approved:
            assert ctx.state()["last_route"] is None  # the old route is stale after a change
            with pytest.raises(NavigationError) as error:
                ctx.assistant.confirm(ctx.sid, proposal, True)
            assert error.value.code == "unknown_proposal"

    return act


def memory_navigation_counts(ctx: Ctx) -> None:
    times = iter(
        datetime(2026, 10, 25, 22, 0, tzinfo=UTC) + timedelta(seconds=30 * i) for i in range(99)
    )
    store = MemoryStore(ctx.tmp / "memory", clock=lambda: next(times))
    info = store.create_session("real")
    for event, data in [
        ("moved", {"xy_m": 100.0, "floor_name": "Ground floor"}),  # preview/camera, no nav
        ("arrival", {"name": "Quality lab 0.21"}),  # cancelled preview "arrival"
        ("nav_start", {}),
        ("moved", {"xy_m": 12.5, "floor_name": "Floor 1"}),
        ("nav_pause", {}),
        ("nav_resume", {}),
        ("arrival", {"name": "Kitchen 1.07", "floor_name": "Floor 1"}),
        ("arrival", {"name": "Kitchen 1.07"}),  # duplicate event after arrival
        ("moved", {"xy_m": 40.0}),  # after the navigation ended
    ]:
        store.append(info.session_id, event, data)  # type: ignore[arg-type]
    recap = compute_recap(*store.read(info.session_id))
    assert recap["walked_m"] == 12.5 and recap["arrivals"] == 1
    assert recap["places_reached"] == ["Kitchen 1.07"] and recap["navigations_completed"] == 1
    assert recap["floors_visited"] == ["Floor 1"] and recap["simulated_minutes"] == 1.5


def memory_deleted_not_resurrected(ctx: Ctx) -> None:
    store = MemoryStore(ctx.tmp / "memory")
    info = store.create_session("real")
    store.append(info.session_id, "nav_start", {})
    store.delete(info.session_id)
    with pytest.raises(SessionNotFoundError):
        store.append(info.session_id, "arrival", {"name": "late event"})
    with pytest.raises(SessionNotFoundError):
        store.read(info.session_id)
    other = store.create_session("real")
    assert other.session_id != info.session_id and store.read(other.session_id)[1] == []


def memory_synthetic_labelled(ctx: Ctx) -> None:
    store = MemoryStore(ctx.tmp / "memory")
    recaps = {}
    for kind in ("real", "demo", "test"):
        info = store.create_session(kind)
        store.append(info.session_id, "nav_start", {})
        store.append(info.session_id, "arrival", {"name": "Office 1.04"})
        recaps[kind] = compute_recap(*store.read(info.session_id))
    assert recaps["real"]["synthetic"] is False and recaps["real"]["label"] == "Your session"
    assert recaps["demo"]["synthetic"] is True and "synthetic" in recaps["demo"]["label"].lower()
    assert recaps["test"]["synthetic"] is True and "test" in recaps["test"]["label"].lower()


# ------------------------------------------------------------------ the scenarios


def _door_widths(ctx: Ctx, space_id: str) -> list[float]:
    data = ctx.inner.building(ctx.building)
    return [o.get("width_m") or 0.0 for o in data["openings"] if space_id in o["space_ids"]]


def _width_ok(ctx: Ctx, result: JSON) -> bool:
    width = ctx.state()["width_m"]
    rows = result["tools"][0]["output"]["results"]
    return all(max(_door_widths(ctx, r["endpoint"]["id"]), default=0.0) >= width for r in rows)


def _s01(heading: float, direction: str, stored: float) -> Scenario:
    return Scenario(
        "S01_" + {90: "north", 0: "east", 180: "west", -90: "south"}[int(heading)],
        "S01",
        f"pose heading {heading} deg (CCW from +X): the corridor door due east is '{direction}'; "
        "the direction filter keeps only that side; heading normalised to [0, 360)",
        [
            Turn(
                [around("all"), around(direction), "The door to the corridor."],
                ui={"pose": POSE | {"heading_deg": heading}},
                expect=[
                    st("pose.heading_deg", stored),
                    tool_out(
                        0,
                        "items",
                        lambda items: any(
                            i["kind"] == "door" and i.get("side") == direction for i in items
                        ),
                        f"corridor door on the {direction}",
                    ),
                    tool(1, "describe_surroundings", filtered_to=direction),
                    tool_out(
                        1,
                        "items",
                        lambda items: (
                            bool(items)
                            and all(i["side"] == direction for i in items)
                            and any(i["kind"] == "door" for i in items)
                        ),
                        f"only {direction} items, door included",
                    ),
                    same("start", "destination"),
                ],
            )
        ],
    )


def _source(source: str | None, expected: str) -> Scenario:
    ui_payload: JSON = {"start": OFFICE} | ({"start_source": source} if source else {})
    return Scenario(
        f"INV_location_source_{expected}",
        "INV-3",
        f"UI start with start_source={source!r} is labelled known_from='{expected}' in the "
        "snapshot and in the session_state the model sees",
        [
            Turn(
                ["You are in Office 1.04."],
                ui=ui_payload,
                expect=[
                    st("start", OFFICE),
                    snap("start.known_from", expected),
                    seen("start.known_from", expected),
                    seen("start.name", "Office 1.04"),
                ],
            )
        ],
    )


SCENARIOS: list[Scenario] = [
    # ---------------------------------------------------------------- C01 state / clearing
    Scenario(
        "C01a",
        "C01",
        "UI start set, then explicitly cleared (start=null): nearest exit asks for the start "
        "(missing_start), no route from the old room, no nearest() call",
        [
            Turn(["Noted."], ui={"start": OFFICE}, expect=[st("start", OFFICE)]),
            Turn(
                [nearest("exit", and_route=True), "Where are you right now?"],
                ui={"start": None},
                expect=[
                    st("start", None),
                    tool(0, "find_nearest", "error", "missing_start"),
                    ui_lacks("route", "endpoints"),
                    not_called("nearest", "route"),
                    snap("start", None),
                ],
            ),
        ],
    ),
    Scenario(
        "C01b",
        "C01",
        "a UI start that the UI no longer sends (key omitted, older clients) is dropped too",
        [
            Turn(["Noted."], ui={"start": OFFICE}),
            Turn(
                [nearest("exit", and_route=True), "Where are you?"],
                expect=[st("start", None), tool(0, "find_nearest", "error", "missing_start")],
            ),
        ],
    ),
    Scenario(
        "C01c",
        "C01",
        "a start the USER stated (update_session) survives turns without a UI start; the exit "
        "route then starts there and ends at an exit door",
        [
            Turn([update(start=OFFICE), "Noted."], expect=[st("start", OFFICE)]),
            Turn(
                [nearest("exit", and_route=True), "Follow the route to the exit."],
                expect=[
                    st("start", OFFICE),
                    tool(0, "find_nearest", from__name="Office 1.04"),
                    tool(0, "find_nearest", route_to_closest__status="ok"),
                    ui("route.target.kind", "opening"),
                    ui("route.start.space_id", OFFICE["id"]),
                ],
            ),
        ],
    ),
    Scenario(
        "C01d",
        "C01",
        "destination=null from the UI clears the destination: plan_route with null endpoints "
        "fails honestly (missing_endpoint) instead of reusing the old destination",
        [
            Turn(["Noted."], ui={"start": OFFICE, "destination": QUALITY_LAB}),
            Turn(
                [route(), "Where would you like to go?"],
                ui={"start": OFFICE, "destination": None},
                expect=[
                    st("destination", None),
                    st("start", OFFICE),
                    tool(0, "plan_route", "error", "missing_endpoint"),
                    ui_lacks("route"),
                    not_called("route"),
                ],
            ),
        ],
    ),
    # ---------------------------------------------------------------- C02 step-free + closure
    Scenario(
        "C02a",
        "C02",
        "wheelchair user, only lift closed (confirmed scenario), destination on another floor; "
        "model passes step_free=false: request stays step-free, unreachable, no stairs, "
        "constraint note returned",
        [
            Turn([update(start=OFFICE, step_free=True), "Noted, step-free."]),
            Turn(
                [route(HALL_A1, step_free=False), "There is no step-free route right now."],
                expect=[
                    st("step_free", True),
                    ui("route_request.step_free", True),
                    ui("route.constraints.step_free", True),
                    tool(0, "plan_route", reachable=False, step_free=True),
                    tool_out(0, "constraint", lambda c: STEP_FREE_NOTE in c, "constraint"),
                    no_stairs(),
                ],
            ),
        ],
        setup=lambda ctx: ctx.close(LIFT),
    ),
    Scenario(
        "C02b",
        "C02",
        "lift closed, find_nearest exit with step_free=false from F1: nearest() still asked "
        "step-free, no exit offered, no route, constraint note",
        [
            Turn([update(start=OFFICE, step_free=True), "Noted."]),
            Turn(
                [nearest("exit", step_free=False, and_route=True), "No step-free way out."],
                expect=[
                    called("nearest", 1, a3=True),
                    tool(0, "find_nearest", results=[]),
                    tool_out(0, "constraint", lambda c: STEP_FREE_NOTE in c, "constraint"),
                    tool_out(0, "route_to_closest", lambda r: r is MISSING, "no route"),
                    ui_lacks("route"),
                    no_stairs(),
                ],
            ),
        ],
        setup=lambda ctx: ctx.close(LIFT),
    ),
    Scenario(
        "C02c",
        "C02",
        "what-if (assume_closed=[lift]) for a step-free user: unreachable, no stairs; the real "
        "scenario is untouched and the next route uses the open lift again",
        [
            Turn(
                [update(start=OFFICE, step_free=True), route(HALL_A1, closed=[LIFT]), "No."],
                expect=[
                    tool(1, "plan_route", reachable=False, assumed_closed=[LIFT]),
                    ui("route_request.assume_closed", [LIFT]),
                    no_stairs(),
                    closed_now(),
                ],
            ),
            Turn(
                [route(), "Take the lift."],
                expect=[
                    ui("route_request.assume_closed", []),
                    tool(0, "plan_route", reachable=True),
                    vertical("lift"),
                ],
            ),
        ],
    ),
    Scenario(
        "C02d",
        "C02",
        "lift closed in precision mode, model drops step_free: failed honestly (no official "
        "fallback, mode stays precision), step-free request, constraint note, no route shown",
        [
            Turn(
                [update(start=OFFICE, step_free=True), route(HALL_A1, step_free=False), "No."],
                ui={"mode": "precision"},
                expect=[
                    tool(1, "plan_route", status="ok", mode="precision"),
                    tool_out(1, "status", lambda s: s == "failed", "failed"),
                    tool_out(1, "constraint", lambda c: STEP_FREE_NOTE in c, "constraint"),
                    ui("route_error.request.step_free", True),
                    ui("route_error.request.mode", "precision"),
                    ui_lacks("route"),
                    st("mode", "precision"),
                    called("route", 1),
                ],
            )
        ],
        setup=lambda ctx: ctx.close(LIFT),
    ),
    # ---------------------------------------------------------------- C03 persistence
    Scenario(
        "C03a",
        "C03",
        "step-free survives a UI mode switch to precision and back and a new turn; the official "
        "route then uses the lift, never the stairs",
        [
            Turn([update(start=OFFICE, step_free=True), "Noted."]),
            Turn(["Precision mode."], ui={"mode": "precision"}, expect=[st("step_free", True)]),
            Turn(
                [route(HALL_A1), "Take the lift down."],
                ui={"mode": "official"},
                expect=[
                    st("step_free", True),
                    st("mode", "official"),
                    ui("route.constraints.step_free", True),
                    vertical("lift"),
                    no_stairs(),
                ],
            ),
        ],
    ),
    Scenario(
        "C03b",
        "C03",
        "UI building switch keeps step-free and width but clears start, destination and the "
        "previous building's conversation",
        [
            Turn([update(start=OFFICE, step_free=True, width_m=0.9), "Noted."]),
            Turn(
                ["You are now in the lecture centre."],
                ui={"building_id": B2},
                expect=[
                    st("building_id", B2),
                    st("start", None),
                    st("destination", None),
                    st("step_free", True),
                    st("width_m", 0.9),
                    custom(
                        "history holds only this turn",
                        lambda c, r: len(c.assistant.session(SID).history) == 2,
                    ),
                ],
            ),
        ],
        needs=(B, B2),
    ),
    Scenario(
        "C03c",
        "C03",
        "only an explicit user change lifts step-free: UI toggle off, back on via "
        "update_session, then the user lifts it; only then a stairs route (lift closed) is ok",
        [
            Turn([update(start=OFFICE, step_free=True), "Noted."]),
            Turn(["Step-free is off."], ui={"step_free": False}, expect=[st("step_free", False)]),
            Turn([update(step_free=True), "Step-free again."], expect=[st("step_free", True)]),
            Turn(
                [update(step_free=False), route(HALL_A1), "Down the stairs."],
                expect=[
                    st("step_free", False),
                    tool(1, "plan_route", reachable=True, step_free=False),
                    tool_out(1, "constraint", lambda c: c is MISSING, "no step-free note"),
                    vertical("stairs"),
                ],
            ),
        ],
        setup=lambda ctx: ctx.close(LIFT),
    ),
    Scenario(
        "C03d",
        "C03",
        "reroute after a scenario change by another consumer: same session endpoints, "
        "step-free still requested, now unreachable without stairs",
        [
            Turn(
                [update(start=OFFICE, step_free=True), route(HALL_A1), "Take the lift."],
                expect=[vertical("lift")],
            ),
            Turn(
                [route(), "The lift is closed; there is no step-free route now."],
                before=lambda ctx: ctx.close(LIFT),
                expect=[
                    ui("route_request.start", OFFICE),
                    ui("route_request.target", HALL_A1),
                    ui("route_request.step_free", True),
                    tool(0, "plan_route", reachable=False),
                    no_stairs(),
                ],
            ),
        ],
    ),
    Scenario(
        "C03e",
        "C03",
        "a precision route echoes the binding width; a UI mode switch keeps width/safety",
        [
            Turn(
                [update(start=CORRIDOR_F0, width_m=0.7, safety_m=0.05), "Noted."],
                ui={"mode": "precision"},
            ),
            Turn(["Official."], ui={"mode": "official"}, expect=[st("width_m", 0.7)]),
            Turn(
                [route(QUALITY_LAB), "Here you go."],
                ui={"mode": "precision"},
                expect=[
                    ui("route_request.width_m", 0.7),
                    ui("route.constraints.width_m", 0.7),
                    ui("route.checked", "obstacle_checked"),
                ],
            ),
        ],
    ),
    # ---------------------------------------------------------------- C04 selection
    Scenario(
        "C04a",
        "C04",
        "inspected machine reaches the model context by name; start/destination untouched",
        [
            Turn(["Noted."], ui={"start": OFFICE}),
            Turn(
                ["That is CNC mill H-A1-A."],
                ui={"start": OFFICE, "inspected": CNC},
                expect=[
                    seen("inspected.name", "CNC mill H-A1-A"),
                    st("inspected", CNC),
                    st("start", OFFICE),
                    st("destination", None),
                    tools(),
                ],
            ),
        ],
    ),
    Scenario(
        "C04b",
        "C04",
        "an unknown inspected item is dropped (not kept from before); the 'selection' alias "
        "works; inspected=null clears",
        [
            Turn(
                ["Okay."],
                ui={"inspected": {"kind": "asset", "id": "nope"}},
                expect=[st("inspected", None), seen("inspected", None)],
            ),
            Turn(
                ["That is the quality lab."],
                ui={"selection": QUALITY_LAB},
                expect=[st("inspected", QUALITY_LAB), snap("inspected.name", "Quality lab 0.21")],
            ),
            Turn(["Okay."], ui={"inspected": None}, expect=[st("inspected", None)]),
        ],
    ),
    # ---------------------------------------------------------------- C05 exact endpoints
    Scenario(
        "C05a",
        "C05",
        "exact map-point start routes to an exit door: point start (requested XY) and final "
        "opening position preserved",
        [
            Turn(
                [route(EXIT_DOOR), "Here is the way out."],
                ui={"start": {"kind": "point", "space_id": OFFICE["id"], "position": [2.5, 2.7]}},
                expect=[
                    st("start.kind", "point"),
                    st("start.position", [2.5, 2.7]),
                    ui("route.start.kind", "point"),
                    ui("route.start.requested_position", [2.5, 2.7]),
                    ui("route.target.kind", "opening"),
                    ui("route.target.id", EXIT_ID),
                    custom(
                        "route ends at the exit door",
                        lambda c, r: door_point(r["ui"]["route"]) == pytest.approx(EXIT_XY),
                    ),
                ],
            )
        ],
    ),
    Scenario(
        "C05b",
        "C05",
        "a map point in an unknown room is refused with a ui_note; no fallback to the earlier "
        "start",
        [
            Turn(["Noted."], ui={"start": OFFICE}),
            Turn(
                ["That point is not in a room. Where are you?"],
                ui={"start": {"kind": "point", "space_id": "nope", "position": [1, 2]}},
                expect=[
                    st("start", None),
                    seen("start", None),
                    custom(
                        "ui_note says unusable",
                        lambda c, r: (
                            "could not be used" in (seen_state(c.transport).get("ui_note") or "")
                        ),
                    ),
                ],
            ),
        ],
    ),
    Scenario(
        "C05c",
        "C05",
        "a map point far outside its room polygon is refused at sync (ui_note), not stored as "
        "'a point in Office 1.04'",
        [
            Turn(
                ["Where are you?"],
                ui={"start": {"kind": "point", "space_id": OFFICE["id"], "position": [500, 500]}},
                expect=[
                    st("start", None),
                    custom(
                        "ui_note says unusable",
                        lambda c, r: (
                            "could not be used" in (seen_state(c.transport).get("ui_note") or "")
                        ),
                    ),
                ],
            )
        ],
        xfail="sync_ui/check_endpoint only checks that the point's space exists, not that the "
        "position lies inside it (the error text claims 'not inside a room'); the bad point is "
        "stored and the router refuses it only later",
    ),
    # ---------------------------------------------------------------- C06 vertical
    Scenario(
        "C06a",
        "C06",
        "successive vertical moves (campus): F0->F4 by lift, then from the arrival room F4->F1; "
        "floors visited in order, the second route starts where the first arrived",
        [
            Turn(
                [route(sp("F4-S002", B2)), "Lift to floor 4."],
                ui={"start": sp("F0-S002", B2), "step_free": True},
                expect=[
                    vertical("lift"),
                    custom("floors F0 -> F4", lambda c, r: _floors(r) == ["F0", "F4"]),
                    tool(0, "plan_route", floor_changes=[{"kind": "lift", "schematic": True}]),
                ],
            ),
            Turn(
                [route(sp("F1-S002", B2)), "Lift down to floor 1."],
                ui={"start": sp("F4-S002", B2), "start_source": "simulation"},
                expect=[
                    ui("route.start.space_id", f"{B2}-F4-S002"),
                    custom("floors F4 -> F1", lambda c, r: _floors(r) == ["F4", "F1"]),
                    ui("route.waypoints.-1.kind", "target"),
                    ui("route.waypoints.-1.space_id", f"{B2}-F1-S002"),
                    snap("start.known_from", "simulation"),
                    no_stairs(),
                ],
            ),
        ],
        building=B2,
        needs=(B2,),
    ),
    # ---------------------------------------------------------------- C07 stale after change
    Scenario(
        "C07a",
        "C07",
        "propose closing the lift changes nothing; Confirm applies it, drops the stale "
        "last_route, the proposal cannot be confirmed twice; the next turn sees the closure",
        [
            Turn(
                [update(start=OFFICE), route(HALL_A1), propose("close", LIFT), "Confirm?"],
                expect=[
                    tool(2, "propose_change", status="ok"),
                    tool_out(2, "status", lambda s: s == "awaiting_user_confirmation", "awaits"),
                    ui("proposal.element_id", LIFT),
                    closed_now(),
                    st("last_route.status", "ok"),
                    custom(
                        "one pending proposal",
                        lambda c, r: len(r["state"]["pending_proposals"]) == 1,
                    ),
                ],
            ),
            Turn(
                ["The office lift is closed now."],
                before=confirm_last(True, "applied"),
                expect=[
                    closed_now(LIFT),
                    snap("confirmed_closures", ["Office lift"]),
                    snap("pending_proposals", []),
                    snap("last_route", None),
                ],
            ),
        ],
    ),
    Scenario(
        "C07b",
        "C07",
        "a cancelled proposal changes nothing and the model is told it was cancelled",
        [
            Turn([propose("close", LIFT), "Shall I close it?"]),
            Turn(
                ["Okay, nothing changed."],
                before=confirm_last(False, "cancelled"),
                expect=[
                    closed_now(),
                    snap("pending_proposals", []),
                    context_has("[I cancelled: close Office lift.]"),
                ],
            ),
        ],
    ),
    # ---------------------------------------------------------------- C08 missing referent
    Scenario(
        "C08a",
        "C08",
        "missing referent ('the biggest one'): a clarifying text-only turn mutates nothing "
        "(state, UI, navigation calls)",
        [
            Turn([update(start=OFFICE), find("kitchen", "room"), "There are several kitchens."]),
            Turn(
                ["The biggest kitchen, or the biggest hall?"],
                text="And the biggest one?",
                expect=[
                    tools(),
                    same("start", "destination", "step_free", "mode", "last_route", "inspected"),
                    ui_lacks("route", "endpoints", "focus", "highlight", "settings"),
                    not_called("search", "nearest", "route", "change", "operation"),
                    custom("reply passed through", lambda c, r: r["reply"].startswith("The big")),
                ],
            ),
        ],
    ),
    Scenario(
        "C08b",
        "C08",
        "'the second one' resolves from the previous turn's ranked results: the model context "
        "carries the found places in order (name = kind:id); the route goes to the second",
        [
            Turn(
                [update(start=OFFICE), nearest("room_type:kitchen"), "Kitchen 1.07 or 1.08."],
                expect=[
                    tool(1, "find_nearest", results__0__endpoint=KITCHEN_107),
                    tool(1, "find_nearest", results__1__endpoint=KITCHEN_108),
                    st("destination", None),
                ],
            ),
            Turn(
                [route(KITCHEN_108), "Kitchen 1.08 is just down the corridor."],
                text="Take me to the second one.",
                expect=[
                    context_has(
                        f"[places found, in order: Kitchen 1.07 = space:{KITCHEN_107['id']}; "
                        f"Kitchen 1.08 = space:{KITCHEN_108['id']}"
                    ),
                    ui("route.target.id", KITCHEN_108["id"]),
                    st("destination", KITCHEN_108),
                    st("start", OFFICE),
                ],
            ),
        ],
    ),
    # ---------------------------------------------------------------- C09 typed endpoints
    Scenario(
        "C09a",
        "C09",
        "search returns an opening (exit) and an asset; routes to them keep the typed endpoint: "
        "the exit route ends at the door, the asset route targets the asset (not room centre)",
        [
            Turn(
                [find("exit", "exit"), route(EXIT_DOOR), "Here is the exit."],
                ui={"start": OFFICE},
                expect=[
                    tool_out(
                        0,
                        "results",
                        lambda rows: any(
                            r["kind"] == "opening" and r["id"] == EXIT_ID for r in rows
                        ),
                        "exit row",
                    ),
                    ui("route.target.kind", "opening"),
                    custom(
                        "route ends at the exit door",
                        lambda c, r: door_point(r["ui"]["route"]) == pytest.approx(EXIT_XY),
                    ),
                ],
            ),
            Turn(
                [route(CNC), "The CNC mill is in hall H-A1."],
                ui={"start": OFFICE},
                expect=[
                    ui("route.target.kind", "asset"),
                    ui("route.target.id", CNC["id"]),
                    ui("route.target.space_id", HALL_A1["id"]),
                    st("destination", CNC),
                ],
            ),
        ],
    ),
    Scenario(
        "C09b",
        "C09",
        "an opening id passed as a space is refused (unknown_place), no router call, no guessing "
        "of another type",
        [
            Turn(
                [route({"kind": "space", "id": EXIT_ID}), "Let me look that up again."],
                ui={"start": OFFICE},
                expect=[
                    tool(0, "plan_route", "error", "unknown_place"),
                    not_called("route"),
                    st("destination", None),
                    ui_lacks("route"),
                ],
            )
        ],
    ),
    # ---------------------------------------------------------------- S01-S03 spatial
    _s01(0, "ahead", 0.0),
    _s01(90, "right", 90.0),
    _s01(180, "behind", 180.0),
    _s01(-90, "left", 270.0),
    Scenario(
        "S02_occluded",
        "S02",
        "server racks in the next room are reported 'behind a wall', never visible; things in "
        "the own room are visible",
        [
            Turn(
                [around("all"), "There is a desk in front of you."],
                ui={"pose": POSE | {"position": [2.5, 4.5], "heading_deg": 90}},
                expect=[
                    tool_out(
                        0,
                        "items",
                        lambda items: any(
                            i.get("room") == "Server room 1.05"
                            and i["visible"] is False
                            and "behind a wall" in i["where"]
                            for i in items
                        ),
                        "occluded rack",
                    ),
                    tool_out(
                        0,
                        "items",
                        lambda items: (
                            not any(
                                i["kind"] == "thing"
                                and i["visible"]
                                and i.get("room") != "Office 1.04"
                                for i in items
                            )
                        ),
                        "nothing from another room visible",
                    ),
                    tool_out(
                        0,
                        "items",
                        lambda items: any(
                            i.get("name") == "Desk 1.04-B" and i["visible"] for i in items
                        ),
                        "desk",
                    ),
                ],
            )
        ],
    ),
    Scenario(
        "S02_no_heading",
        "S02",
        "unknown view direction: no sides are given and the result tells the model to ask; the "
        "direction filter is not applied",
        [
            Turn(
                [around("right"), "Which way are you facing?"],
                ui={"pose": POSE | {"heading_deg": None}},
                expect=[
                    snap("pose.view_direction_known", False),
                    tool(0, "describe_surroundings", heading_known=False),
                    tool_out(
                        0,
                        "items",
                        lambda items: bool(items) and all("side" not in i for i in items),
                        "no sides",
                    ),
                    tool_out(0, "note", lambda n: "ask" in n, "asks"),
                    tool_out(0, "filtered_to", lambda f: f is MISSING, "unfiltered"),
                ],
            )
        ],
    ),
    Scenario(
        "S02_no_pose",
        "S02",
        "no pose (cleared): describe_surroundings refuses (missing_pose) instead of guessing",
        [
            Turn(["Okay."], ui={"pose": POSE | {"heading_deg": 0}}),
            Turn(
                [around("right"), "Where are you?"],
                ui={"pose": None},
                expect=[
                    st("pose", None),
                    seen("pose", MISSING),
                    tool(0, "describe_surroundings", "error", "missing_pose"),
                    not_called("surroundings"),
                ],
            ),
        ],
    ),
    Scenario(
        "S03_leak_below",
        "S03",
        "leak in Office 1.04: room_stacked below = the largest-overlap room on F0 (Hall access "
        "0.19), highlighted; no route, no state change",
        [
            Turn(
                [fact("room_stacked", start=OFFICE["id"], direction="below"), "Hall access 0.19."],
                expect=[
                    tool(0, "building_fact", space_name="Hall access 0.19", space_ref="0.19"),
                    ui("highlight", [f"{B}-F0-S019"]),
                    ui_lacks("route"),
                    same("start", "destination"),
                ],
            )
        ],
    ),
    Scenario(
        "S03_no_floor_below",
        "S03",
        "room_stacked below the ground floor and malformed arguments are honest errors "
        "(invalid_input), nothing invented",
        [
            Turn(
                [
                    fact("room_stacked", start=QUALITY_LAB["id"], direction="below"),
                    fact("room_stacked", raw="{not json"),
                    "There is no floor below the ground floor.",
                ],
                expect=[
                    tool(0, "building_fact", "error", "invalid_input"),
                    tool(1, "building_fact", "error", "invalid_input"),
                    ui_lacks("highlight", "operation"),
                ],
            )
        ],
    ),
    Scenario(
        "S05_chitchat",
        "S05",
        "a joke/thanks turn without tools passes the reply through and mutates nothing "
        "(no navigation call, state and step-free unchanged)",
        [
            Turn([update(start=OFFICE, step_free=True), "Noted."]),
            Turn(
                ["Why did the robot cross the hall? To find the lift!"],
                text="Tell me a joke.",
                expect=[
                    tools(),
                    custom("reply passed through", lambda c, r: r["reply"].startswith("Why")),
                    same("start", "destination", "step_free", "last_route"),
                    not_called("search", "nearest", "route", "change", "surroundings"),
                    ui_lacks("route", "endpoints", "settings"),
                ],
            ),
        ],
    ),
    # ---------------------------------------------------------------- H01-H03 memory/sessions
    Scenario(
        "H01_only_navigation_counts",
        "H01",
        "activity recap counts only motion/arrivals inside a navigation; preview motion, a "
        "duplicate arrival and motion after arrival do not count; paused time excluded",
        custom=memory_navigation_counts,
    ),
    Scenario(
        "H02_reset_and_isolation",
        "H02",
        "two assistant sessions are isolated; a reset session comes back without the old pose, "
        "start, step-free or history",
        [
            Turn(
                [update(step_free=True), "Noted."],
                ui={"start": OFFICE, "pose": POSE | {"heading_deg": 0}},
                expect=[st("step_free", True), st("pose.space_id", OFFICE["id"])],
            ),
            Turn(
                ["Hello!"],
                sid="scenario-0002",
                expect=[
                    st("start", None),
                    st("pose", None),
                    st("step_free", False),
                    custom("session 1 untouched", lambda c, r: c.state(SID)["start"] == OFFICE),
                ],
            ),
            Turn(
                ["Hello again!"],
                before=lambda ctx: ctx.assistant.reset(SID),
                expect=[
                    st("start", None),
                    st("pose", None),
                    st("step_free", False),
                    custom(
                        "model context has no earlier turns",
                        lambda c, r: len(requests(c)[0]["messages"]) == 1,
                    ),
                    custom(
                        "no old history", lambda c, r: len(c.assistant.session(SID).history) == 2
                    ),
                ],
            ),
        ],
    ),
    Scenario(
        "H02_deleted_history_not_resurrected",
        "H02",
        "a deleted activity session cannot be appended to or read again; new ids are fresh",
        custom=memory_deleted_not_resurrected,
    ),
    Scenario(
        "H03_synthetic_labelled",
        "H03",
        "demo and test sessions are labelled synthetic in the recap; real ones are not",
        custom=memory_synthetic_labelled,
    ),
    # ---------------------------------------------------------------- invariants
    Scenario(
        "INV_search_does_not_move_start",
        "INV-2",
        "find_places and find_nearest (no route) only highlight: start and destination stay, "
        "no endpoints/route in the UI payload, no router call",
        [
            Turn([update(start=OFFICE, destination=QUALITY_LAB), "Noted."]),
            Turn(
                [find("hand dryer", "equipment"), nearest("asset_type:hand_dryer"), "Two."],
                expect=[
                    same("start", "destination"),
                    st("start", OFFICE),
                    st("destination", QUALITY_LAB),
                    ui_lacks("route", "endpoints"),
                    custom("highlighted", lambda c, r: bool(r["ui"].get("highlight"))),
                    not_called("route"),
                ],
            ),
        ],
    ),
    Scenario(
        "INV_preview_closure_changes_nothing",
        "INV-2",
        "preview_closure for a step-free user reports consequences but changes neither the "
        "scenario, the revision, the session nor creates a proposal",
        [
            Turn(
                [update(start=OFFICE, step_free=True), preview(LIFT), "47 rooms would be cut off."],
                expect=[
                    tool(1, "preview_closure", step_free=True),
                    tool_out(1, "rooms_unreachable_after", lambda n: n > 0, "> 0"),
                    closed_now(),
                    custom("revision unchanged", lambda c, r: c.inner.state(B)["revision"] == 0),
                    snap("pending_proposals", []),
                    ui_lacks("proposal", "route"),
                    st("start", OFFICE),
                ],
            )
        ],
    ),
    Scenario(
        "INV_clear_is_real",
        "INV-1",
        "'Clear simulated location' (start=null, pose=null) really clears: surroundings and "
        "nearest refuse, the model sees no start/pose",
        [
            Turn(
                ["Noted."],
                ui={
                    "start": OFFICE,
                    "start_source": "simulation",
                    "pose": POSE | {"heading_deg": 0},
                },
                expect=[st("start", OFFICE), snap("pose.known_from", "simulated navigation")],
            ),
            Turn(
                [around("right"), nearest("exit", and_route=True), "Where are you?"],
                ui={"start": None, "pose": None},
                expect=[
                    st("start", None),
                    st("pose", None),
                    seen("start", None),
                    seen("pose", MISSING),
                    tool(0, "describe_surroundings", "error", "missing_pose"),
                    tool(1, "find_nearest", "error", "missing_start"),
                    ui_lacks("route"),
                ],
            ),
        ],
    ),
    Scenario(
        "INV_step_free_tightens_only",
        "INV-4",
        "session step-free + find_nearest(step_free=false, and_route): nearest() and the route "
        "request are step-free; the route uses the lift",
        [
            Turn([update(start=OFFICE, step_free=True), "Noted."]),
            Turn(
                [nearest("room_type:toilet", step_free=False, and_route=True), "Toilet 0.22."],
                expect=[
                    called("nearest", 1, a3=True),
                    ui("route_request.step_free", True),
                    ui("route.constraints.step_free", True),
                    tool(0, "find_nearest", route_to_closest__step_free=True),
                    vertical("lift"),
                    st("step_free", True),
                ],
            ),
        ],
    ),
    Scenario(
        "INV_step_free_tool_tightens_persists",
        "INV-4",
        "a tool argument step_free=true tightens the session and the next route stays step-free",
        [
            Turn(
                [update(start=OFFICE), route(HALL_A1, step_free=True), "Via the lift."],
                expect=[st("step_free", True), vertical("lift")],
            ),
            Turn(
                [route(QUALITY_LAB), "Via the lift again."],
                expect=[ui("route_request.step_free", True), vertical("lift"), no_stairs()],
            ),
        ],
    ),
    Scenario(
        "INV_step_free_guard_defence",
        "INV-4",
        "fault injection: a router that ignores step_free returns a stairs route; the assistant's "
        "guard turns it into 'no step-free route', shows no route and records a failed route",
        [
            Turn(
                [update(start=OFFICE, step_free=True), route(HALL_A1), "No step-free route."],
                expect=[
                    called("route", 1, step_free=True),
                    custom(
                        "router really returned stairs",
                        lambda c, r: RouterIgnoresStepFree.injected_stairs,
                    ),
                    tool(1, "plan_route", explanation="No step-free route was found."),
                    tool_out(1, "status", lambda s: s == "failed", "failed"),
                    tool_out(1, "constraint", lambda c: STEP_FREE_NOTE in c, "constraint"),
                    ui_lacks("route"),
                    st("last_route", {"mode": "official", "status": "failed"}),
                ],
            )
        ],
        nav=RouterIgnoresStepFree,
    ),
    Scenario(
        "INV_lift_closed_no_stairs_fallback",
        "INV-4",
        "lift closed, step-free nearest toilet with route: the same-floor toilet is chosen "
        "(the closer one downstairs is not offered via stairs), no vertical transition",
        [
            Turn(
                [
                    update(start=OFFICE, step_free=True),
                    nearest("room_type:toilet", and_route=True),
                    "Toilet 1.32 on this floor.",
                ],
                expect=[
                    tool_out(
                        1,
                        "results",
                        lambda rows: [x["name"] for x in rows] == ["Toilet 1.32"],
                        "only Toilet 1.32",
                    ),
                    tool(1, "find_nearest", route_to_closest__status="ok"),
                    tool(1, "find_nearest", route_to_closest__floor_changes=[]),
                    ui("route.target.id", TOILET_F1["id"]),
                    vertical(),
                    no_stairs(),
                ],
            )
        ],
        setup=lambda ctx: ctx.close(LIFT),
    ),
    Scenario(
        "INV_route_constraints_echoed",
        "INV-4",
        "precision + step-free + width/safety from the UI: the route request and the route's "
        "own constraints echo exactly the session settings (guidance reroutes reuse them)",
        [
            Turn(
                [route(QUALITY_LAB), "Here you go."],
                ui={
                    "start": CORRIDOR_F0,
                    "mode": "precision",
                    "step_free": True,
                    "width_m": 0.6,
                    "safety_m": 0.05,
                },
                expect=[
                    ui(
                        "route.constraints",
                        {
                            "step_free": True,
                            "profile": "stationary",
                            "width_m": 0.6,
                            "safety_m": 0.05,
                        },
                    ),
                    ui("route_request.width_m", 0.6),
                    ui("route_request.step_free", True),
                    ui("route.mode", "precision"),
                    tool(0, "plan_route", checked="obstacle_checked", step_free=True),
                ],
            )
        ],
    ),
    Scenario(
        "INV_width_no_silent_fallback",
        "INV-4",
        "precision with a 1.0 m width that no door path supports: failed with the 'offer "
        "official' hint, no silent official route, width and mode unchanged, one router call",
        [
            Turn(
                [route(QUALITY_LAB), "No obstacle-checked route at that width."],
                ui={"start": CORRIDOR_F0, "mode": "precision", "width_m": 1.0},
                expect=[
                    tool_out(0, "status", lambda s: s == "failed", "failed"),
                    tool_out(0, "hint", lambda h: h.startswith("Offer the official"), "hint"),
                    ui_lacks("route"),
                    ui("route_error.request.width_m", 1.0),
                    st("mode", "precision"),
                    st("width_m", 1.0),
                    called("route", 1, mode="precision"),
                ],
            )
        ],
    ),
    Scenario(
        "INV_width_binding_nearest",
        "INV-4",
        "precision mode with a required width of 1.0 m: find_nearest must not rank kitchens "
        "whose only doors are 0.9 m as reachable",
        [
            Turn(
                [nearest("room_type:kitchen"), "Kitchen 0.28."],
                ui={"start": CORRIDOR_F0, "mode": "precision", "width_m": 1.0},
                expect=[custom("every result passable at the session width", _width_ok)],
            )
        ],
        xfail="find_nearest ignores the session width (NavigationPort.nearest has no width "
        "parameter): Kitchen 0.28 behind a 0.9 m door is ranked first for a 1.0 m width",
    ),
    _source("simulation", "simulation"),
    _source(None, "manual"),
    _source("qr", "qr"),
    _source("hypothesis", "hypothesis"),
    Scenario(
        "INV_location_source_unknown_not_manual",
        "INV-3",
        "an unsupported source (e.g. an experimental image estimate) must not be labelled "
        "'manual' (= user-confirmed)",
        [
            Turn(
                ["Okay."],
                ui={"start": OFFICE, "start_source": "image_estimate"},
                expect=[
                    custom(
                        "not claimed as manual",
                        lambda c, r: r["state"]["start"].get("known_from") != "manual",
                    ),
                ],
            )
        ],
        xfail="sync_ui maps any unknown start_source to 'manual', so an unverified estimate is "
        "presented to the model as a manually confirmed position",
    ),
    Scenario(
        "INV_location_source_after_update_session",
        "INV-3",
        "a start the user states after an earlier QR start must not inherit the stale 'qr' label",
        [
            Turn(
                ["Noted."],
                ui={"start": OFFICE, "start_source": "qr"},
                expect=[snap("start.known_from", "qr")],
            ),
            Turn(
                [update(start=OFFICE_106), "Noted, Office 1.06."],
                expect=[
                    snap("start.name", "Office 1.06"),
                    custom(
                        "labelled as the user's statement",
                        lambda c, r: (
                            r["state"]["start"].get("known_from") in {"manual", "hypothesis"}
                        ),
                    ),
                ],
            ),
        ],
        xfail="update_session (and plan_route) set state.start but never start_source; the "
        "previous UI source ('qr') stays and is shown as known_from for the new start",
    ),
    Scenario(
        "INV_stale_pose_rejected",
        "INV-5",
        "invalid poses are discarded, never kept from the previous turn: foreign-building room, "
        "non-finite position; a non-finite heading becomes 'unknown'",
        [
            Turn(
                ["Okay."],
                ui={"pose": POSE | {"heading_deg": 90}},
                expect=[st("pose.heading_deg", 90.0)],
            ),
            Turn(
                ["Okay."],
                ui={"pose": {"space_id": f"{B2}-F1-S002", "position": [1, 1], "heading_deg": 0}},
                expect=[st("pose", None), seen("pose", MISSING)],
            ),
            Turn(
                ["Okay."],
                ui={"pose": POSE | {"position": [math.nan, 1.0], "heading_deg": 0}},
                expect=[st("pose", None)],
            ),
            Turn(
                ["Which way are you facing?"],
                ui={"pose": POSE | {"heading_deg": math.inf}},
                expect=[st("pose.heading_deg", None), snap("pose.view_direction_known", False)],
            ),
        ],
    ),
    Scenario(
        "INV_pose_outside_room_rejected",
        "INV-5",
        "a pose whose position lies far outside its claimed room is discarded",
        [
            Turn(
                ["Okay."],
                ui={"pose": POSE | {"position": [40.0, 60.0], "heading_deg": 0}},
                expect=[st("pose", None)],
            )
        ],
        xfail="_check_pose only checks that the space id exists and the XY is finite; a pose 60 m "
        "away from Office 1.04 is accepted and describe_surroundings answers from it",
    ),
    Scenario(
        "INV_route_preview_does_not_move_user",
        "INV-2",
        "'how far is it from the kitchen to the lab?' (plan_route with an explicit other start) "
        "must not relocate the user: the next nearest search still starts at Office 1.04",
        [
            Turn([update(start=OFFICE), "Noted."]),
            Turn(
                [
                    route(QUALITY_LAB, start=KITCHEN_028),
                    nearest("room_type:toilet"),
                    "About 40 m; the nearest toilet is downstairs.",
                ],
                expect=[
                    st("start", OFFICE),
                    tool(1, "find_nearest", from__name="Office 1.04"),
                ],
            ),
        ],
        xfail="tool_plan_route writes an explicit start into session.state['start'] (and keeps "
        "the old start_source), so a route between two other places moves the user",
    ),
    Scenario(
        "INV_unknown_room_honest_error",
        "INV-7",
        "unknown room ids from the model give unknown_place for route and update_session; start "
        "and destination are untouched; no router call",
        [
            Turn([update(start=OFFICE), "Noted."]),
            Turn(
                [
                    route(sp("F9-S999")),
                    update(start={"kind": "space", "id": "nope"}),
                    "I can't find that room. Which room do you mean?",
                ],
                expect=[
                    tool(0, "plan_route", "error", "unknown_place"),
                    tool(1, "update_session", "error", "unknown_place"),
                    st("start", OFFICE),
                    st("destination", None),
                    not_called("route"),
                    ui_lacks("route"),
                ],
            ),
        ],
    ),
    Scenario(
        "INV_building_switch_by_tool",
        "INV-5",
        "select_building: unknown id refused; a real switch clears start, proposals and history "
        "(the old proposal cannot be confirmed); an unknown UI building is refused and the "
        "state stays",
        [
            Turn([update(start=OFFICE), propose("close", LIFT), "Confirm?"]),
            Turn(
                [select("no-such-building"), select(B2), "You are in the lecture centre now."],
                expect=[
                    tool(0, "select_building", "error", "unknown_building"),
                    tool(1, "select_building"),
                    ui("building_id", B2),
                    st("building_id", B2),
                    st("start", None),
                    st("step_free", False),
                    custom("no proposals", lambda c, r: not c.assistant.session(SID).proposals),
                    custom(
                        "history holds only this turn",
                        lambda c, r: len(c.assistant.session(SID).history) == 2,
                    ),
                ],
            ),
            Turn(
                ["(never sent)"],
                ui={"building_id": "no-such-building"},
                raises="unknown_building",
                before=lambda ctx: _old_proposal_is_gone(ctx),
                expect=[st("building_id", B2)],
            ),
        ],
        needs=(B, B2),
    ),
]


def _old_proposal_is_gone(ctx: Ctx) -> None:
    proposal = ctx.outs[0]["ui"]["proposal"]["proposal_id"]
    with pytest.raises(NavigationError) as error:
        ctx.assistant.confirm(SID, proposal, True)
    assert error.value.code == "unknown_proposal"
    assert ctx.closed() == []


def _floors(result: JSON) -> list[str]:
    floors: list[str] = []
    for point in result["ui"]["route"]["waypoints"]:
        level = point["floor_id"].rsplit("-", 1)[-1]
        if not floors or floors[-1] != level:
            floors.append(level)
    return floors


# ------------------------------------------------------------------ runner


@functools.cache
def building_file(building_id: str) -> BuildingFile | None:
    for directory in DATASET_DIRS:
        path = directory / building_id / "building.json"
        if path.is_file():
            return BuildingFile.model_validate_json(path.read_bytes())
    return None


def make_navigation(building_ids: tuple[str, ...]) -> NavigationService:
    repository = InMemoryBuildingRepository()
    for building_id in building_ids:
        data = building_file(building_id)
        assert data is not None
        repository.save(building_id, data.model_copy(deep=True))
    return NavigationService(repository, DATASET_DIRS[0])


def run_scenario(scenario: Scenario, tmp: Path) -> None:
    missing = [b for b in scenario.needs if building_file(b) is None]
    if missing:
        pytest.skip(f"building data not found: {missing} (SYNERA_DEV_DATA={DEV})")
    available = tuple(b for b in (B, B2) if building_file(b) is not None)
    inner = make_navigation(available)
    nav = scenario.nav(inner)
    RouterIgnoresStepFree.injected_stairs = False
    ctx = Ctx(Assistant(nav), nav, inner, scenario.building, tmp)
    if scenario.setup:
        scenario.setup(ctx)
    if scenario.custom:
        scenario.custom(ctx)
        return
    for number, turn in enumerate(scenario.turns, 1):
        ctx.sid = turn.sid
        if turn.before:
            turn.before(ctx)
        payload = {"building_id": scenario.building} | turn.ui
        ctx.transport = Scripted(turn.script)
        ctx.assistant.transport = ctx.transport
        before = copy.deepcopy(ctx.state(turn.sid))
        nav.calls.clear()
        if turn.raises:
            with pytest.raises(NavigationError) as error:
                ctx.assistant.turn(turn.sid, turn.text, payload)
            assert error.value.code == turn.raises
            out: JSON = {"tools": [], "ui": {}, "state": {}}
        else:
            out = ctx.assistant.turn(turn.sid, turn.text, payload)
            assert ctx.transport.script == [], f"turn {number}: scripted calls left unused"
        ctx.outs.append(out)
        for check in turn.expect:
            try:
                check(ctx, out, before)
            except (AssertionError, KeyError, IndexError, TypeError) as failure:
                raise AssertionError(
                    f"{scenario.id} turn {number} [{check.__name__}]: {failure!r}"
                ) from failure


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(
            s,
            id=s.id,
            marks=[pytest.mark.xfail(strict=True, reason=s.xfail)] if s.xfail else [],
        )
        for s in SCENARIOS
    ],
)
def test_dialog_scenario(scenario: Scenario, tmp_path: Path) -> None:
    run_scenario(scenario, tmp_path)


def test_scenario_table_is_well_formed() -> None:
    ids = [s.id for s in SCENARIOS]
    assert len(ids) == len(set(ids)), "duplicate scenario ids"
    assert 40 <= len(SCENARIOS) <= 60
    for s in SCENARIOS:
        assert bool(s.turns) != bool(s.custom), s.id
        for turn in s.turns:
            if not turn.raises:
                assert turn.script and isinstance(turn.script[-1], str), f"{s.id}: ends in text"

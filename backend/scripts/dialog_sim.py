"""Multi-turn customer-dialog matrix against the real AriNav assistant (`app.assistant.Assistant`).

Ported from the prototype's `sim/run_sim.py` (prototypes/unified-nav-voice). The dialogs live in
`scripts/dialog_scenarios.py`; every check is deterministic and never calls a model. Buildings are
ingested into an in-memory repository behind one real `NavigationService`; only Claude is either
live (`--live`, costs money) or replaced by a fixed-text stand-in (`--scripted`, free, proves the
harness plumbing only: most checks fail by design).

Run from backend/:
    .venv/Scripts/python scripts/dialog_sim.py --scripted
    .venv/Scripts/python scripts/dialog_sim.py --live --max-usd 1.0 --label live_medium
    .venv/Scripts/python scripts/dialog_sim.py --live --only coffee_nearest,closure_flow
Options: --model, --effort, --voice (mark turns as spoken), --datasets-dir DIR (repeatable).
Writes docs/reports/dialog_sim_<label>.json (every turn: input, ui, reply, tools, state, route
summary, usage, timing, verdicts) and .md (per-scenario / per-turn pass table, cost, latency).

Budget: before each live turn the harness stops when `spent + most expensive turn so far` would
exceed --max-usd; everything not run is listed as skipped. API errors are never retried; they are
recorded as failed turns (the partial cost of such a turn is taken from the session usage).
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import statistics
import sys
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:  # allow `python scripts/dialog_sim.py`
    sys.path.insert(0, str(BACKEND_DIR))

from app.assistant.service import EFFORT, MODEL, PRICES, Assistant  # noqa: E402
from app.ingestion import ingest_dataset  # noqa: E402
from app.navigation import NavigationService  # noqa: E402
from app.navigation.errors import NavigationError  # noqa: E402
from app.storage import InMemoryBuildingRepository  # noqa: E402
from scripts.dialog_scenarios import SCENARIOS  # noqa: E402

JSON = dict[str, Any]
REPORTS_DIR = BACKEND_DIR.parent / "docs" / "reports"
DEFAULT_DATASET_DIRS = [
    BACKEND_DIR / "data" / "datasets",
    Path.home() / "Documents" / "HackathonTransfer" / "SyneraKit-data" / "dev",
]
ID_PATTERN = re.compile(r"proc-[a-z]+-|-F-?\d+-S\d{3}|\bA\d{5}\b|\bO\d{4}\b|osm-way|swiss-\d")
NEGATION = re.compile(
    r"\b(no|not|isn't|aren't|can't|cannot|couldn't|doesn't|don't|unfortunately|unable)\b", re.I
)
ASKS = re.compile(r"\b(tell me|let me know|which one|say where)\b", re.I)
# Nearest-by-walking-cost categories (NavigationService.nearest categories, unioned).
CATEGORIES = {
    "coffee": ["asset_type:coffee_machine", "room_type:kitchen", "room_type:canteen"],
    "toilet": ["room_type:toilet"],
}
COST_TIE_M = 0.01
SCRIPTED_TEXT = "Scripted reply: this offline run only exercises the dialog harness."


# ------------------------------------------------------------------ transports


class FixedReply:
    """Offline stand-in for Claude: every request answers with the same text, no tools, no cost."""

    def __init__(self, text: str = SCRIPTED_TEXT) -> None:
        self.text = text
        self.calls = 0

    def create(self, **_kwargs: Any) -> JSON:
        self.calls += 1
        return {
            "stop_reason": "end_turn",
            "usage": {},
            "content": [{"type": "text", "text": self.text}],
        }


# ------------------------------------------------------------------ check helpers


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s]


def holding(data: JSON, endpoint: JSON | None) -> str | None:
    """The space an endpoint is in (an asset's room; a space is itself; doors keep their id)."""
    if not endpoint:
        return None
    if endpoint.get("kind") == "asset":
        return next(
            (a.get("space_id") for a in data["assets"] if a["id"] == endpoint.get("id")), None
        )
    ident: str | None = endpoint.get("id")
    return ident


def route_request(ui: JSON) -> JSON | None:
    """The RouteRequest of this turn's last route (successful or failed)."""
    request = ui.get("route_request") or (ui.get("route_error") or {}).get("request")
    return request if isinstance(request, dict) else None


def tool_used(tools: Iterable[JSON], name: str) -> bool:
    """`name` was called; find_nearest with and_route=true counts as plan_route (it computes and
    shows the route in the same step)."""
    for t in tools:
        if t.get("tool") == name:
            return True
        if (
            name == "plan_route"
            and t.get("tool") == "find_nearest"
            and (t.get("input") or {}).get("and_route")
        ):
            return True
    return False


def nearest_spaces(
    navigation: NavigationService,
    building_id: str,
    start: JSON | None,
    category: str,
    step_free: bool,
) -> tuple[set[str], float | None]:
    """Spaces holding the closest `category` place by OFFICIAL walking cost (ties within 1 cm)."""
    if not start:
        return set(), None
    rows: list[tuple[float, str]] = []
    for name in CATEGORIES[category]:
        try:
            found = navigation.nearest(building_id, start, name, step_free, 5)
        except NavigationError:
            continue
        rows += [(r["walk_cost_m"], r["space_id"]) for r in found if r.get("space_id")]
    if not rows:
        return set(), None
    best = min(cost for cost, _ in rows)
    return {space for cost, space in rows if cost - best <= COST_TIE_M}, best


def check(
    expect: JSON,
    out: JSON,
    *,
    navigation: NavigationService,
    building_id: str,
    state: JSON,
    closed_before: set[str],
) -> tuple[bool, JSON]:
    """Deterministic verdicts for one turn. `state` is the raw session state after the turn."""
    reply = out.get("reply") or ""
    tools: list[JSON] = out.get("tools") or []
    ui: JSON = out.get("ui") or {}
    data = navigation.building(building_id)
    names = {s["id"]: s.get("name") or s["id"] for s in data["spaces"]}
    route = ui.get("route") if isinstance(ui.get("route"), dict) else None
    request = route_request(ui)
    closed = set(navigation.state(building_id)["closed"])
    verdicts: JSON = {
        "no_error": not out.get("error"),
        "no_ids_in_reply": not ID_PATTERN.search(reply),
    }
    for key, value in expect.items():
        if key == "tools":
            verdicts[key] = all(tool_used(tools, name) for name in value)
        elif key == "tools_any":
            verdicts[key] = any(tool_used(tools, name) for name in value)
        elif key == "state_start":
            verdicts[key] = holding(data, state.get("start")) == value
        elif key == "state_step_free":
            verdicts[key] = state.get("step_free") is value
        elif key == "state_mode":
            verdicts[key] = state.get("mode") == value
        elif key == "state_width":
            verdicts[key] = abs(float(state.get("width_m") or 0) - value) < 1e-6
        elif key == "short":
            verdicts[key] = len(sentences(reply)) <= 3 and len(reply) <= 350
        elif key == "asks":
            verdicts[key] = "?" in reply or bool(ASKS.search(reply))
        elif key == "no_route":
            verdicts[key] = route is None
        elif key == "route_found":
            verdicts[key] = (
                route is not None
                and route.get("status") == "ok"
                and route.get("reachable", True) is not False
            )
        elif key == "route_unreachable":
            verdicts[key] = tool_used(tools, "plan_route") and (
                route is None
                or route.get("reachable") is False
                or route.get("status") in {"unreachable", "blocked"}
            )
        elif key == "route_from":
            start = (request or {}).get("start") or {}
            verdicts[key] = bool(request) and (
                start.get("id") == value or holding(data, start) == value
            )
        elif key == "route_to":
            target = (request or {}).get("target") or {}
            wanted = {value, holding(data, {"kind": "asset", "id": value})} - {None}
            verdicts[key] = bool(request) and target.get("id") in wanted
        elif key in ("mentions_nearest", "route_to_nearest"):
            want, cost = nearest_spaces(
                navigation, building_id, state.get("start"), value, bool(state.get("step_free"))
            )
            if key == "mentions_nearest":
                hit = False
                for space in want:
                    label = names.get(space, "")
                    number = re.search(r"\d+\.\d+|[A-Z]-[A-Z]\d", label)
                    hit = hit or label.lower() in reply.lower()
                    hit = hit or bool(number and number.group() in reply)
                verdicts[key] = bool(want) and hit
            else:
                target = (request or {}).get("target")
                verdicts[key] = bool(request) and bool(want) and holding(data, target) in want
            verdicts[key + "_expected"] = sorted(names.get(s, s) for s in want) or None
            verdicts[key + "_expected_cost_m"] = cost
        elif key == "mentions_any":
            verdicts[key] = any(word.lower() in reply.lower() for word in value)
        elif key == "honest_missing":
            verdicts[key] = bool(NEGATION.search(reply)) and route is None
        elif key == "previewed_closed":
            verdicts[key] = any(
                value
                in list((t.get("input") or {}).get("assume_closed") or [])
                + list((t.get("input") or {}).get("element_ids") or [])
                for t in tools
                if t.get("tool") in ("plan_route", "preview_closure")
            )
        elif key == "unchanged":
            verdicts[key] = closed == closed_before
        elif key == "proposal":
            proposal = ui.get("proposal") or {}
            verdicts[key] = [proposal.get("action"), proposal.get("element_id")] == list(value)
        elif key == "closed_now":
            verdicts[key] = value in closed
        elif key == "open_now":
            verdicts[key] = value not in closed
        elif key == "uses":
            verdicts[key] = any(
                t.get("kind") == value for t in (route or {}).get("transitions") or []
            )
        else:
            verdicts[key] = None  # unknown check: reported, not counted
    passed = all(v for k, v in verdicts.items() if is_verdict(k) and v is not None)
    return passed, verdicts


def is_verdict(key: str) -> bool:
    return not (key.endswith("_expected") or key.endswith("_expected_cost_m"))


# ------------------------------------------------------------------ records


def route_summary(route: JSON | None) -> JSON | None:
    if not isinstance(route, dict):
        return None
    keep = (
        "mode",
        "status",
        "reachable",
        "checked",
        "length_m",
        "walk_m",
        "vertical_cost_m",
        "revision",
        "start",
        "target",
        "space_path",
        "element_path",
        "directions",
        "unreachable_reason",
        "warnings",
        "closed",
        "assume_closed",
    )
    out: JSON = {k: route.get(k) for k in keep if k in route}
    out["transitions"] = [
        {k: t.get(k) for k in ("kind", "element_id", "from_floor_id", "to_floor_id", "schematic")}
        for t in route.get("transitions") or []
    ]
    return out


def ui_record(ui: JSON) -> JSON:
    out = {k: v for k, v in ui.items() if k != "route"}
    if "route" in ui:
        out["route"] = route_summary(ui["route"])
    return out


def usage_cost(model: str, before: JSON, after: JSON) -> float:
    """Estimated USD of the session usage delta (used for turns that raised mid-way)."""
    price = PRICES[model]
    delta = {k: (after.get(k) or 0) - (before.get(k) or 0) for k in after}
    return (
        delta.get("input_tokens", 0) * price["input"]
        + delta.get("output_tokens", 0) * price["output"]
        + delta.get("cache_read_input_tokens", 0) * price["cache_read"]
        + delta.get("cache_creation_input_tokens", 0) * price["cache_write"]
    ) / 1e6


# ------------------------------------------------------------------ setup


def find_dataset(dirs: Iterable[Path], dataset: str) -> Path | None:
    return next((d for d in dirs if (d / dataset / "building.json").is_file()), None)


def load_buildings(
    building_ids: Iterable[str], dirs: list[Path]
) -> tuple[NavigationService, dict[str, str]]:
    """One in-memory repository + NavigationService with every findable building ingested.
    Returns the service and `{building_id: source dir}` (missing buildings are absent)."""
    repository = InMemoryBuildingRepository()
    sources: dict[str, str] = {}
    for building_id in dict.fromkeys(building_ids):
        found = find_dataset(dirs, building_id)
        if found is not None:
            ingest_dataset(repository, found, building_id)
            sources[building_id] = str(found)
    return NavigationService(repository, dirs[0] if dirs else None), sources


def select(scenarios: list[JSON], only: str | None) -> list[JSON]:
    if not only:
        return list(scenarios)
    wanted = [s.strip() for s in only.split(",") if s.strip()]
    known = {s["id"] for s in scenarios}
    unknown = [w for w in wanted if w not in known]
    if unknown:
        raise SystemExit(f"Unknown scenario id(s): {', '.join(unknown)}")
    return [s for s in scenarios if s["id"] in wanted]


# ------------------------------------------------------------------ run


def run(
    scenarios: list[JSON],
    assistant: Assistant,
    navigation: NavigationService,
    *,
    available: set[str],
    max_usd: float,
    spoken: bool = False,
    log: Any = print,
) -> tuple[list[JSON], JSON]:
    """Run the dialogs in order. Returns (scenario records, run totals)."""
    results: list[JSON] = []
    spent = 0.0
    turn_costs: list[float] = []
    stopped = False
    skipped: list[JSON] = []
    for number, scenario in enumerate(scenarios):
        building_id = scenario["building"]
        session_id = f"dialog-sim-{number:04d}-{scenario['id']}"
        record: JSON = {
            "id": scenario["id"],
            "title": scenario["title"],
            "building": building_id,
            "session_id": session_id,
            "turns": [],
        }
        if building_id not in available:
            for index, turn in enumerate(scenario["turns"]):
                record["turns"].append(
                    {"index": index, "user": turn["user"], "skipped": "building_missing"}
                )
                skipped.append(
                    {"scenario": scenario["id"], "turn": index, "reason": "building_missing"}
                )
            record["result"] = "skipped"
            results.append(record)
            log(f"SKIP  {scenario['id']} (building {building_id} not found)")
            continue
        state = navigation.state(building_id)  # each dialog starts with nothing closed
        if state["closed"]:
            navigation.change(building_id, "reset", None, state["revision"])
        assistant.reset(session_id)
        for index, turn in enumerate(scenario["turns"]):
            estimate = max(turn_costs, default=0.0)
            if stopped or spent + estimate > max_usd:
                stopped = True
                record["turns"].append({"index": index, "user": turn["user"], "skipped": "budget"})
                skipped.append({"scenario": scenario["id"], "turn": index, "reason": "budget"})
                continue
            record["turns"].append(
                run_turn(assistant, navigation, scenario, index, turn, session_id, spoken)
            )
            cost = record["turns"][-1]["cost_usd"]
            spent += cost
            turn_costs.append(cost)
        run_turns = [t for t in record["turns"] if "skipped" not in t]
        if not run_turns:
            record["result"] = "skipped"
        elif len(run_turns) < len(record["turns"]):
            record["result"] = "partial"
        else:
            record["result"] = "pass" if all(t["passed"] for t in run_turns) else "fail"
        results.append(record)
        log(f"{record['result'].upper():<5} {scenario['id']}  (spent ${spent:.4f})")
    return results, {"estimated_usd": round(spent, 5), "skipped": skipped}


def run_turn(
    assistant: Assistant,
    navigation: NavigationService,
    scenario: JSON,
    index: int,
    turn: JSON,
    session_id: str,
    spoken: bool,
) -> JSON:
    building_id = scenario["building"]
    session = assistant.session(session_id)
    closed_before = set(navigation.state(building_id)["closed"])
    context = assistant.snapshot(session) if session.state["building_id"] else None
    usage_before = dict(session.usage)
    ui_sent: JSON = {"building_id": building_id, **(turn.get("ui") or {})}
    began = time.perf_counter()
    try:
        out = assistant.turn(session_id, turn["user"], ui_sent, spoken=spoken)
        cost = float((out.get("usage") or {}).get("estimated_usd") or 0.0)
    except Exception as error:  # AssistantError (API, no retry) or a bug: a failed turn
        out = {
            "reply": "",
            "status": "error",
            "tools": [],
            "ui": {},
            "error": f"{type(error).__name__}: {error}",
            "usage": {},
            "timing_ms": {},
        }
        cost = usage_cost(assistant.model, usage_before, session.usage)
    wall = (time.perf_counter() - began) * 1000
    state = copy.deepcopy(session.state)
    passed, verdicts = check(
        turn["expect"],
        out,
        navigation=navigation,
        building_id=building_id,
        state=state,
        closed_before=closed_before,
    )
    ui = out.get("ui") or {}
    entry: JSON = {
        "index": index,
        "user": turn["user"],
        "input": {"text": turn["user"], "ui": ui_sent, "spoken": spoken},
        "context_before": context,
        "expected": turn["expect"],
        "passed": passed,
        "verdicts": verdicts,
        "reply": out.get("reply"),
        "status": out.get("status"),
        "error": out.get("error"),
        "phases": out.get("phases"),
        "tools": out.get("tools") or [],
        "ui": ui_record(ui),
        "state": out.get("state"),
        "session_state": state,
        "closed_after": sorted(navigation.state(building_id)["closed"]),
        "usage": out.get("usage"),
        "cost_usd": round(cost, 6),
        "timing_ms": {**(out.get("timing_ms") or {}), "wall_ms": round(wall, 1)},
    }
    if "confirm" in turn:
        proposal = ui.get("proposal")
        if not proposal:
            entry["confirmation"] = {"approved": turn["confirm"], "status": "no_proposal"}
        else:
            try:
                done = assistant.confirm(session_id, proposal["proposal_id"], turn["confirm"])
                entry["confirmation"] = {
                    "approved": turn["confirm"],
                    "status": done["status"],
                    "message": done.get("message"),
                }
            except Exception as error:  # recorded, not raised: the matrix must finish
                entry["confirmation"] = {
                    "approved": turn["confirm"],
                    "status": "failed",
                    "error": f"{type(error).__name__}: {error}",
                }
    return entry


# ------------------------------------------------------------------ reports


def median(values: list[float]) -> float | None:
    return round(statistics.median(values), 1) if values else None


def summarize(results: list[JSON], totals: JSON, meta: JSON) -> JSON:
    turns = [t for r in results for t in r["turns"] if "skipped" not in t]
    claude = [float(t["timing_ms"].get("claude_ms", 0)) for t in turns if not t.get("error")]
    tools_ms = [float(t["timing_ms"].get("tools_ms", 0)) for t in turns if not t.get("error")]
    run_scenarios = [r for r in results if r["result"] in ("pass", "fail")]
    return {
        **meta,
        "scenarios": len(results),
        "scenarios_run": len(run_scenarios),
        "scenarios_passed": sum(r["result"] == "pass" for r in results),
        "scenarios_partial": sum(r["result"] == "partial" for r in results),
        "scenarios_skipped": sum(r["result"] == "skipped" for r in results),
        "turns": sum(len(r["turns"]) for r in results),
        "turns_run": len(turns),
        "turns_passed": sum(bool(t["passed"]) for t in turns),
        "turns_errored": sum(bool(t.get("error")) for t in turns),
        "turns_skipped": len(totals["skipped"]),
        "estimated_usd": totals["estimated_usd"],
        "latency_ms": {
            "claude_median": median(claude),
            "claude_max": round(max(claude), 1) if claude else None,
            "tools_median": median(tools_ms),
            "wall_median": median([float(t["timing_ms"]["wall_ms"]) for t in turns]),
        },
        "concise_1_2_sentences": sum(
            1 for t in turns if t.get("reply") and len(sentences(t["reply"])) <= 2
        ),
        "skipped": totals["skipped"],
    }


def cell(text: Any, limit: int = 90) -> str:
    value = " ".join(str(text or "").split()).replace("|", "\\|")
    return value if len(value) <= limit else value[: limit - 1] + "…"


def markdown(summary: JSON, results: list[JSON]) -> str:
    latency = summary["latency_ms"]
    lines = [
        f"# Dialog simulation `{summary['label']}`",
        "",
        f"- Mode: **{summary['mode']}** · model `{summary['model']}` · effort "
        f"`{summary['effort']}` · spoken: {summary['spoken']}",
        f"- Scenarios passed: **{summary['scenarios_passed']}/{summary['scenarios']}** "
        f"(run {summary['scenarios_run']}, partial {summary['scenarios_partial']}, "
        f"skipped {summary['scenarios_skipped']})",
        f"- Turns passed: **{summary['turns_passed']}/{summary['turns_run']}** run "
        f"({summary['turns_errored']} errored, {summary['turns_skipped']} skipped)",
        f"- Total estimated cost: **${summary['estimated_usd']:.4f}** "
        f"(budget ${summary['max_usd']:.2f})",
        f"- Median Claude latency: **{latency['claude_median']} ms** "
        f"(max {latency['claude_max']} ms; tools median {latency['tools_median']} ms; "
        f"wall median {latency['wall_median']} ms)",
        f"- Replies with 1-2 sentences: {summary['concise_1_2_sentences']}",
        f"- Started: {summary['started_at']} · buildings: "
        + ", ".join(f"`{b}`" for b in summary["buildings"]),
        "",
        "## Scenarios",
        "",
        "| Scenario | Building | Result | Turns passed | Failed checks |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        run_turns = [t for t in r["turns"] if "skipped" not in t]
        failed = sorted({k for t in run_turns for k, v in t["verdicts"].items() if v is False})
        lines.append(
            f"| {r['id']} | {r['building']} | {r['result'].upper()} | "
            f"{sum(bool(t['passed']) for t in run_turns)}/{len(r['turns'])} | "
            f"{', '.join(failed)} |"
        )
    lines += [
        "",
        "## Turns",
        "",
        "| Scenario | # | User | Result | Failed checks | Tools | Reply | USD | Claude ms |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        for t in r["turns"]:
            if "skipped" in t:
                lines.append(
                    f"| {r['id']} | {t['index'] + 1} | {cell(t['user'], 60)} | "
                    f"SKIPPED ({t['skipped']}) |  |  |  |  |  |"
                )
                continue
            failed = [k for k, v in t["verdicts"].items() if v is False]
            tools = ", ".join(f"{x['tool']}({x.get('status')})" for x in t["tools"])
            reply = t["error"] if t.get("error") else t["reply"]
            confirm = t.get("confirmation")
            result = "PASS" if t["passed"] else "FAIL"
            if confirm:
                result += f" · confirm={confirm['status']}"
            lines.append(
                f"| {r['id']} | {t['index'] + 1} | {cell(t['user'], 60)} | {result} | "
                f"{', '.join(failed)} | {cell(tools, 80)} | {cell(reply, 120)} | "
                f"{t['cost_usd']:.4f} | {t['timing_ms'].get('claude_ms', '')} |"
            )
    if summary["skipped"]:
        lines += ["", "## Skipped", ""]
        lines += [
            f"- {s['scenario']} turn {s['turn'] + 1}: {s['reason']}" for s in summary["skipped"]
        ]
    lines += [
        "",
        "Checks are deterministic (scripts/dialog_sim.py); the full per-turn record (input, ui, "
        "reply, tools, state, route summary, usage, timing, verdicts) is in the JSON report.",
    ]
    return "\n".join(lines) + "\n"


def write_reports(out_dir: Path, label: str, summary: JSON, results: list[JSON]) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"dialog_sim_{label}.json"
    md_path = out_dir / f"dialog_sim_{label}.md"
    json_path.write_text(
        json.dumps(
            {"summary": summary, "results": results}, indent=2, ensure_ascii=False, default=str
        )
        + "\n",
        encoding="utf-8",
    )
    md_path.write_text(markdown(summary, results), encoding="utf-8")
    return [json_path, md_path]


# ------------------------------------------------------------------ CLI


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="real Claude (costs money)")
    mode.add_argument("--scripted", action="store_true", help="offline fixed-text stand-in")
    parser.add_argument("--only", help="comma-separated scenario ids")
    parser.add_argument("--max-usd", type=float, default=1.0)
    parser.add_argument("--label", help="report name (default: live / scripted)")
    parser.add_argument("--model", default=MODEL, choices=sorted(PRICES))
    parser.add_argument("--effort", default=EFFORT, choices=["low", "medium", "high"])
    parser.add_argument("--voice", action="store_true", help="mark turns as spoken")
    parser.add_argument(
        "--datasets-dir",
        type=Path,
        action="append",
        help="folder with <building_id>/building.json (repeatable; default: backend/data/"
        "datasets, then ~/Documents/HackathonTransfer/SyneraKit-data/dev)",
    )
    parser.add_argument("--out-dir", type=Path, default=REPORTS_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if args.live and not key:
        print(
            "Refusing to run live: ANTHROPIC_API_KEY is not set in the environment. "
            "Use --scripted for the free offline harness check.",
            file=sys.stderr,
        )
        return 2
    label = args.label or ("live" if args.live else "scripted")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", label):
        print("--label may only contain letters, digits, '_', '-' and '.'", file=sys.stderr)
        return 2
    scenarios = select(SCENARIOS, args.only)
    dirs = [d.resolve() for d in (args.datasets_dir or DEFAULT_DATASET_DIRS)]
    navigation, sources = load_buildings((s["building"] for s in scenarios), dirs)
    if args.live:
        from app.assistant.transport import LiveTransport

        transport: Any = LiveTransport(key)
    else:
        transport = FixedReply()
    assistant = Assistant(navigation, transport, model=args.model, effort=args.effort)
    started = datetime.now(UTC).isoformat(timespec="seconds")
    results, totals = run(
        scenarios,
        assistant,
        navigation,
        available=set(sources),
        max_usd=args.max_usd,
        spoken=args.voice,
    )
    meta = {
        "label": label,
        "mode": "live" if args.live else "scripted",
        "model": args.model,
        "effort": args.effort,
        "spoken": args.voice,
        "max_usd": args.max_usd,
        "started_at": started,
        "buildings": sorted(sources),
        "dataset_dirs": [str(d) for d in dirs],
        "missing_buildings": sorted({s["building"] for s in scenarios} - set(sources)),
    }
    summary = summarize(results, totals, meta)
    paths = write_reports(args.out_dir, label, summary, results)
    shown = {k: v for k, v in summary.items() if k != "skipped"}
    print(json.dumps(shown, indent=2))
    if summary["skipped"]:
        print(f"Skipped {len(summary['skipped'])} turn(s):")
        for s in summary["skipped"]:
            print(f"  {s['scenario']} turn {s['turn'] + 1}: {s['reason']}")
    for path in paths:
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

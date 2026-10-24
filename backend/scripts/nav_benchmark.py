"""Deterministic offline benchmark harness for kit 1.1.0 against `app.navigation.NavigationService`.

Ported from the prototype's `benchmark_eval.py` (prototypes/unified-nav-voice). No model or network
calls. The Train/Dev buildings are loaded from the official kit ZIP into an in-memory
BuildingRepository (each building.json verified against the kit 1.1.0 blob id, validated as a
`BuildingFile` exactly like ingestion does) and served by a fresh NavigationService.

Sections:
- benchmark: every questions.jsonl item through `NavigationService.operation` and the vendored
  official scorer (`app.navigation.official.score.score_reply`);
- routes: questions_basic.jsonl route / route_step_free / route_with_closure / tour through
  `NavigationService.route` in official mode, with endpoints, closure and stops taken from the
  structured answer fields (as the kit's own `starter_agent.evaluate --oracle` does). An item is
  correct when reachability agrees and, if reachable, space_path and element_path are identical and
  the length is within 0.01 m (the 1 % column is reported as well);
- locate / count: plain lookups on the building data, parsed from the question wording.

Run from backend/:
    .venv/Scripts/python scripts/nav_benchmark.py
    .venv/Scripts/python scripts/nav_benchmark.py --splits dev --sections benchmark routes
    .venv/Scripts/python scripts/nav_benchmark.py --out ../docs/reports/nav_benchmark_1_1_0
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from io import StringIO
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:  # allow `python scripts/nav_benchmark.py`
    sys.path.insert(0, str(BACKEND_DIR))

from app.domain.building import BuildingFile  # noqa: E402
from app.navigation import NavigationService, kit  # noqa: E402
from app.navigation.errors import NavigationError  # noqa: E402
from app.navigation.official import KIT_COMMIT, KIT_VERSION  # noqa: E402
from app.navigation.official import score as official_score  # noqa: E402
from app.storage import InMemoryBuildingRepository  # noqa: E402

JSON = dict[str, Any]
ROUTE_KINDS = ("route", "route_step_free", "route_with_closure", "tour")
SECTIONS = ("benchmark", "routes", "locate", "count")
LENGTH_RELATIVE = 0.01
DEFAULT_OUT = BACKEND_DIR.parent / "docs" / "reports" / "nav_benchmark_1_1_0"


# ------------------------------------------------------------------ building source


class KitBuildings:
    """Loads kit Train/Dev buildings on demand into an in-memory repository behind one service."""

    def __init__(self) -> None:
        self.repository = InMemoryBuildingRepository()
        self.service = NavigationService(self.repository, datasets_dir=None)
        self._sources: dict[str, str] | None = None

    def ensure(self, building_id: str) -> str:
        if self.repository.get(building_id) is None:
            raw = kit.load_building_bytes(building_id)
            self.repository.save(f"kit:{building_id}", BuildingFile.model_validate_json(raw))
        return building_id

    def building(self, building_id: str) -> JSON:
        return self.service.building(self.ensure(building_id))

    def source(self, building_id: str) -> str | None:
        if self._sources is None:
            text = kit.read_member("data/manifest.csv").decode("utf-8-sig")
            self._sources = {r["building_id"]: r["source"] for r in csv.DictReader(StringIO(text))}
        return self._sources.get(building_id)


def _short(value: Any, limit: int = 12) -> Any:
    if isinstance(value, list) and len(value) > limit:
        return value[:limit] + [f"... {len(value) - limit} more"]
    return value


def _record(section: str, question: JSON, split: str, source: str | None, kind: str) -> JSON:
    return {
        "section": section,
        "id": question["id"],
        "split": split,
        "building": question["building_id"],
        "source": source,
        "kind": kind,
        "expected": question["answer"],
    }


# ------------------------------------------------------------------ benchmark questions


def run_benchmark(kb: KitBuildings, question: JSON, split: str) -> JSON:
    bid = question["building_id"]
    record = _record("benchmark", question, split, kb.source(bid), question["kind"])
    arguments = copy.deepcopy(question.get("inputs", {}))
    record.update(
        inputs=question.get("inputs"),
        tool="NavigationService.operation",
        request={"operation": question["kind"], "arguments": arguments},
    )
    try:
        result = kb.service.operation(kb.ensure(bid), question["kind"], copy.deepcopy(arguments))
    except NavigationError as error:
        record.update(
            ok=False,
            actual={"error": error.code, "message": error.message},
            cause="error:" + error.code,
        )
        return record
    reply = result["formatted_answer"]
    ok = bool(official_score.score_reply(question["answer"], reply))
    actual = {
        "formatted_answer": reply,
        "value": result.get("value"),
        "ids": _short(result.get("ids", [])),
    }
    for key in ("via", "target"):
        if key in result:
            actual[key] = result[key]
    record.update(ok=ok, actual=actual, cause=None if ok else _benchmark_cause(question, result))
    return record


def _benchmark_cause(question: JSON, result: JSON) -> str:
    answer = question["answer"]
    if answer.get("type") == "number" and isinstance(result.get("value"), int | float):
        return "count_off" if answer.get("relative_tolerance", 0) == 0 else "value_off"
    if answer.get("type") == "space":
        return "wrong_space_or_area"
    return "wrong_value"


# ------------------------------------------------------------------ warm-up: locate / count


def _floor_by_phrase(building: JSON, phrase: str) -> JSON | None:
    phrase = phrase.strip().lower()
    hits = [f for f in building["floors"] if f.get("name", "").lower() == phrase]
    return hits[0] if len(hits) == 1 else None


def _label(asset: JSON) -> str:
    """Asset display label: its name without the trailing room/instance ref."""
    name = str(asset.get("name", "")).strip().lower()
    return name.rsplit(" ", 1)[0] if " " in name else name


def _types_for_phrase(building: JSON, phrase: str) -> set[str]:
    """Asset types a wording refers to: exact type ('toilet'), else the display label."""
    by_type = {
        a["type"]
        for a in building["assets"]
        if a.get("type", "").replace("_", " ").lower() == phrase
    }
    return by_type or {a["type"] for a in building["assets"] if _label(a) == phrase}


def run_locate(building: JSON, question: JSON, split: str, source: str | None) -> JSON:
    """'Where is the <thing>?' -> the unique asset with that exact name, type or display label."""
    match = re.match(
        r"^\s*Where is (?:the |a |an )?(.+?)\s*\?\s*$", question["question"], re.IGNORECASE
    )
    phrase = match.group(1).strip().lower() if match else None
    by_name = [a for a in building["assets"] if phrase and a.get("name", "").lower() == phrase]
    types = _types_for_phrase(building, phrase) if phrase else set()
    candidates = by_name or [a for a in building["assets"] if a.get("type") in types]
    floors = {f["id"]: f for f in building["floors"]}
    expected = question["answer"]
    record = _record("warmup", question, split, source, "locate")
    record.update(
        inputs={"phrase": phrase},
        tool="asset lookup by exact name or type",
        request={"phrase": phrase},
    )
    if len(candidates) != 1:
        record.update(
            ok=False,
            actual={"candidates": _short([a["id"] for a in candidates])},
            cause="no_match" if not candidates else "ambiguous_phrase",
        )
        return record
    asset = candidates[0]
    actual = {
        "asset_id": asset["id"],
        "space_id": asset["space_id"],
        "floor_level": floors[asset["floor_id"]]["level"],
    }
    ok = all(actual[k] == expected.get(k) for k in actual)
    record.update(ok=ok, actual=actual, cause=None if ok else "different_asset")
    return record


def run_count(building: JSON, question: JSON, split: str, source: str | None) -> JSON:
    """'How many <type> items are on the <floor name>?' -> assets of that type on that floor."""
    match = re.match(
        r"^\s*How many (.+?) items are on (?:the )?(.+?)\s*\?\s*$",
        question["question"],
        re.IGNORECASE,
    )
    expected = question["answer"]
    record = _record("warmup", question, split, source, "count")
    record["tool"] = "asset count by type and floor"
    if not match:
        record.update(inputs=None, request=None, ok=False, actual=None, cause="unparsed_question")
        return record
    phrase = match.group(1).strip().lower()
    types = _types_for_phrase(building, phrase)
    asset_type = next(iter(types)) if len(types) == 1 else phrase.replace(" ", "_")
    floor = _floor_by_phrase(building, match.group(2))
    record.update(
        inputs={"phrase": phrase, "floor": match.group(2)},
        request={"asset_type": asset_type, "floor_id": floor and floor["id"]},
    )
    if floor is None or len(types) > 1:
        record.update(
            ok=False,
            actual={"types": sorted(types)},
            cause="unknown_floor" if floor is None else "ambiguous_phrase",
        )
        return record
    count = sum(
        1
        for a in building["assets"]
        if a.get("type") == asset_type and a.get("floor_id") == floor["id"]
    )
    actual = {"count": count, "asset_type": asset_type, "floor_level": floor["level"]}
    ok = actual == {k: expected.get(k) for k in actual}
    record.update(ok=ok, actual=actual, cause=None if ok else "count_off")
    return record


# ------------------------------------------------------------------ warm-up: routes


def route_request(question: JSON) -> JSON:
    """RouteRequest (official mode) from a warm-up question's structured answer fields."""
    answer = question["answer"]

    def space(sid: str) -> JSON:
        return {"kind": "space", "id": sid}

    request: JSON = {"mode": "official"}
    if question["kind"] == "tour":
        stops = answer["stops"]
        request.update(
            start=space(stops[0]),
            via=[space(s) for s in stops[1:-1]],
            target=space(stops[-1]),
        )
    else:
        request.update(start=space(answer["from_space_id"]), target=space(answer["to_space_id"]))
    request["step_free"] = question["kind"] == "route_step_free"
    closed = answer.get("closed_element_id")
    request["assume_closed"] = [closed] if closed else []
    return request


def _expected_legs(answer: JSON, kind: str) -> list[tuple[Any, Any, Any]]:
    if kind == "tour":
        return [
            (leg["space_path"], leg["element_path"], leg["optimal_length_m"])
            for leg in answer["legs"]
        ]
    return [(answer["space_path"], answer["element_path"], answer["optimal_length_m"])]


def run_route(kb: KitBuildings, question: JSON, split: str) -> JSON:
    bid = question["building_id"]
    request = route_request(question)
    answer = question["answer"]
    expected_reachable = answer.get("reachable") is not False
    record = _record("warmup", question, split, kb.source(bid), question["kind"])
    record.update(
        inputs={
            k: answer[k]
            for k in ("from_space_id", "to_space_id", "closed_element_id", "stops")
            if k in answer
        },
        tool="NavigationService.route(mode=official)",
        request=request,
    )
    checks: JSON = {"expected_reachable": expected_reachable}
    try:
        result = kb.service.route(kb.ensure(bid), copy.deepcopy(request))
    except NavigationError as error:
        record.update(
            ok=False,
            checks=checks,
            actual={"error": error.code, "message": error.message},
            cause="error:" + error.code,
        )
        return record
    reachable = bool(result.get("reachable"))
    cost: Any = result.get("length_m")
    is_tour = question["kind"] == "tour"
    actual_legs = (
        [
            (leg.get("space_path"), leg.get("element_path"), leg.get("cost_m"))
            for leg in result.get("legs") or []
        ]
        if is_tour
        else [(result.get("space_path"), result.get("element_path"), cost)]
    )
    actual: JSON = {
        "reachable": reachable,
        "status": result.get("status"),
        "length_m": cost,
        "unreachable_reason": result.get("unreachable_reason"),
    }
    if is_tour:
        actual["legs"] = [
            {"space_path": s, "element_path": e, "cost_m": c} for s, e, c in actual_legs
        ]
    else:
        actual.update(space_path=result.get("space_path"), element_path=result.get("element_path"))
    checks["reachability_agrees"] = reachable == expected_reachable
    cause: str | None
    if expected_reachable and reachable:
        wanted = _expected_legs(answer, question["kind"])
        same_count = len(wanted) == len(actual_legs)
        pairs = list(zip(wanted, actual_legs, strict=False))
        checks["space_path"] = same_count and all(w[0] == a[0] for w, a in pairs)
        checks["element_path"] = same_count and all(w[1] == a[1] for w, a in pairs)
        optimal = float(answer["optimal_length_m"])
        good = isinstance(cost, int | float) and math.isfinite(cost)
        checks["length_within_1pct"] = (
            good and abs(cost - optimal) <= LENGTH_RELATIVE * optimal + 1e-9
        )
        checks["length_within_0_01m"] = good and abs(cost - optimal) <= 0.01 + 1e-9
        checks["length_equal_2dp"] = good and round(cost, 2) == round(optimal, 2)
        checks["length_error_m"] = (cost - optimal) if good else None
        if is_tour and same_count:
            # Tour answers sum the rounded legs; the official route() rounds the total once.
            checks["legs_length_equal_2dp"] = all(
                isinstance(a[2], int | float) and round(a[2], 2) == round(w[2], 2) for w, a in pairs
            )
        walks = [
            s for s in result.get("steps", []) if s.get("type") == "walk" and "drawn_length_m" in s
        ]
        if walks:  # drawable geometry replays each official walk edge (3-decimal weights)
            checks["drawn_walks_match_cost"] = all(
                abs(s["drawn_length_m"] - s["cost_m"]) <= 6e-4 for s in walks if s.get("clear_walk")
            )
            checks["fallback_walks"] = sum(not s.get("clear_walk") for s in walks)
        ok = bool(checks["space_path"] and checks["element_path"] and checks["length_within_0_01m"])
        if ok:
            cause = None
        elif not checks["space_path"]:
            cause = "different_space_path"
        elif not checks["element_path"]:
            cause = "different_element_path"
        elif checks["length_within_1pct"]:
            cause = "length_off_under_1pct"
        else:
            cause = "length_off_over_1pct"
    else:
        ok = bool(checks["reachability_agrees"])
        cause = (
            None
            if ok
            else (
                "expected_unreachable_got_route" if reachable else "expected_route_got_unreachable"
            )
        )
    record.update(ok=ok, checks=checks, actual=actual, cause=cause)
    return record


# ------------------------------------------------------------------ summary


def _rate(ok: int, total: int) -> JSON:
    return {"ok": ok, "total": total}


def _rate_of(rows: Iterable[JSON]) -> JSON:
    rows = list(rows)
    return _rate(sum(bool(r["ok"]) for r in rows), len(rows))


def _route_stats(rows: list[JSON]) -> JSON:
    checks = [r.get("checks", {}) for r in rows]
    reachable = [c for c in checks if c.get("expected_reachable")]
    unreachable = [c for c in checks if c.get("expected_reachable") is False]
    both = [c for c in checks if "space_path" in c]
    errors = sorted(abs(c["length_error_m"]) for c in both if c.get("length_error_m") is not None)

    def count(rows_: list[JSON], key: str) -> int:
        return sum(bool(c.get(key)) for c in rows_)

    return {
        "expected_reachable": len(reachable),
        "expected_unreachable": len(unreachable),
        "reachability_agrees": _rate(count(checks, "reachability_agrees"), len(rows)),
        "reachability_agrees_expected_reachable": _rate(
            count(reachable, "reachability_agrees"), len(reachable)
        ),
        "reachability_agrees_expected_unreachable": _rate(
            count(unreachable, "reachability_agrees"), len(unreachable)
        ),
        "compared_reachable_pairs": len(both),
        "space_path_exact": _rate(count(both, "space_path"), len(reachable)),
        "element_path_exact": _rate(count(both, "element_path"), len(reachable)),
        "length_within_1pct": _rate(count(both, "length_within_1pct"), len(reachable)),
        "length_within_0_01m": _rate(count(both, "length_within_0_01m"), len(reachable)),
        "length_equal_2dp": _rate(count(both, "length_equal_2dp"), len(reachable)),
        "tour_legs_length_equal_2dp": _rate(
            count(both, "legs_length_equal_2dp"), sum("legs_length_equal_2dp" in c for c in both)
        ),
        "drawn_geometry_matches_cost": _rate(
            count(both, "drawn_walks_match_cost"), sum("drawn_walks_match_cost" in c for c in both)
        ),
        "routes_with_blocked_walk_fallback": count(both, "fallback_walks"),
        "max_abs_length_error_m": errors[-1] if errors else None,
        "median_abs_length_error_m": errors[len(errors) // 2] if errors else None,
    }


def summarise(records: list[JSON]) -> JSON:
    bench = [r for r in records if r["section"] == "benchmark"]
    warm = [r for r in records if r["section"] == "warmup"]
    out: JSON = {"benchmark": {"overall": _rate_of(bench)}, "warmup": {}}
    for key in ("split", "kind", "source"):
        groups: dict[str, list[JSON]] = defaultdict(list)
        for r in bench:
            groups[str(r[key])].append(r)
        out["benchmark"]["by_" + key] = {k: _rate_of(v) for k, v in sorted(groups.items())}
    for kind in ("locate", "count", *ROUTE_KINDS):
        rows = [r for r in warm if r["kind"] == kind]
        entry: JSON = {
            "overall": _rate_of(rows),
            "by_split": {
                s: _rate_of(r for r in rows if r["split"] == s)
                for s in sorted({r["split"] for r in rows})
            },
        }
        if kind in ROUTE_KINDS:
            entry.update(_route_stats(rows))
        out["warmup"][kind] = entry
    routes = [r for r in warm if r["kind"] in ROUTE_KINDS]
    out["warmup"]["all_routes"] = {"overall": _rate_of(routes), **_route_stats(routes)}
    no_tour = [r for r in routes if r["kind"] != "tour"]
    out["warmup"]["routes_without_tours"] = {"overall": _rate_of(no_tour), **_route_stats(no_tour)}
    sources: dict[str, list[JSON]] = defaultdict(list)
    for r in routes:
        sources[str(r["source"])].append(r)
    out["warmup"]["routes_by_source"] = {k: _rate_of(v) for k, v in sorted(sources.items())}
    failures = [r for r in records if not r["ok"]]
    out["failures_by_cause"] = dict(
        Counter(f"{r['section']}:{r['kind']}:{r['cause']}" for r in failures).most_common()
    )
    out["failures_by_source"] = dict(
        Counter(f"{r['section']}:{r['source']}" for r in failures).most_common()
    )
    return out


def headline(summary: JSON) -> dict[str, JSON]:
    """The four reference numbers: benchmark, warm-up routes, locate, count."""
    return {
        "benchmark": summary["benchmark"]["overall"],
        "routes": summary["warmup"]["all_routes"]["overall"],
        "locate": summary["warmup"]["locate"]["overall"],
        "count": summary["warmup"]["count"]["overall"],
    }


def _display(path: Path) -> str:
    """A path for the committed report, relative to the home directory when possible."""
    try:
        return "~/" + path.resolve().relative_to(Path.home().resolve()).as_posix()
    except ValueError:
        return str(path)


def _fmt(rate: JSON) -> str:
    return f"{rate['ok']}/{rate['total']}"


def markdown(report: JSON) -> str:
    s = report["summary"]
    rt = report["runtime_s"]
    timing = ", ".join(f"{k} {v:.1f} s" for k, v in rt.items() if k != "total")
    lines = [
        f"# Navigation benchmark vs kit {report['kit']['version']} answers",
        "",
        f"`app.navigation.NavigationService` (backend), kit {report['kit']['version']} "
        f"(commit `{report['kit']['commit']}`); buildings from the kit ZIP via an in-memory "
        "BuildingRepository, questions from the verified kit checkout. Offline, deterministic, "
        f"no model calls. Runtime {rt['total']:.1f} s ({timing}). "
        f"Splits: {', '.join(report['splits'])}. Sections: {', '.join(report['sections'])}.",
        "",
        "| Section | Correct | Prototype reference |",
        "|---|---|---|",
    ]
    for key, rate in report["headline"].items():
        if rate["total"]:
            ref = report["reference"].get(key)
            lines.append(f"| {key} | **{_fmt(rate)}** | {_fmt(ref) if ref else '-'} |")
    if "benchmark" in report["sections"]:
        lines += [
            "",
            "## Benchmark questions (official scorer)",
            "",
            f"Overall **{_fmt(s['benchmark']['overall'])}**; "
            + ", ".join(f"{k} {_fmt(v)}" for k, v in s["benchmark"]["by_split"].items()),
            "",
            "| Kind | Correct |",
            "|---|---|",
        ]
        lines += [f"| {k} | {_fmt(v)} |" for k, v in s["benchmark"]["by_kind"].items()]
        lines += [
            "",
            "By source: "
            + ", ".join(f"{k} {_fmt(v)}" for k, v in s["benchmark"]["by_source"].items()),
        ]
    lines += [
        "",
        "## Warm-up questions",
        "",
        "| Kind | Correct | Reachability agrees | space_path exact | element_path exact "
        "| length <=1 % | length <=0.01 m |",
        "|---|---|---|---|---|---|---|",
    ]
    for kind in ("locate", "count", *ROUTE_KINDS, "routes_without_tours", "all_routes"):
        v = s["warmup"][kind]
        if not v["overall"]["total"]:
            continue
        if "reachability_agrees" in v:
            lines.append(
                f"| {kind} | {_fmt(v['overall'])} | {_fmt(v['reachability_agrees'])} "
                f"| {_fmt(v['space_path_exact'])} | {_fmt(v['element_path_exact'])} "
                f"| {_fmt(v['length_within_1pct'])} | {_fmt(v['length_within_0_01m'])} |"
            )
        else:
            lines.append(f"| {kind} | {_fmt(v['overall'])} | - | - | - | - | - |")
    if "routes" in report["sections"]:
        w = s["warmup"]
        lines += [
            "",
            'Route rows: "Correct" = reachability agrees and, if reachable, identical space_path '
            "and element_path and length within 0.01 m (prototype criterion). Path/length columns "
            "count against expected-reachable questions.",
            f"Exact to the centimetre: routes without tours "
            f"{_fmt(w['routes_without_tours']['length_equal_2dp'])}; tours: every leg "
            f"{_fmt(w['tour']['tour_legs_length_equal_2dp'])}, total "
            f"{_fmt(w['tour']['length_equal_2dp'])} (a tour answer sums the rounded legs, the "
            "official route() rounds the total once).",
            f"Drawn walk segments equal the official walk edge lengths (+-0.6 mm) on "
            f"{_fmt(w['all_routes']['drawn_geometry_matches_cost'])} reachable routes; routes with "
            "a blocked-walk fallback edge: "
            f"{w['all_routes']['routes_with_blocked_walk_fallback']}.",
            "",
            "Routes by source: "
            + ", ".join(f"{k} {_fmt(v)}" for k, v in w["routes_by_source"].items()),
        ]
    lines += ["", "## Failures by cause", ""]
    if s["failures_by_cause"]:
        lines += ["| Section:kind:cause | Count |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in s["failures_by_cause"].items()]
        lines += ["", "### Failures (first 50; all in the JSON)", ""]
        lines += [
            f"- `{r['id']}` ({r['kind']}, {r['source']}): {r['cause']}"
            for r in report["failures"][:50]
        ]
    else:
        lines.append("None.")
    lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ run

# Prototype reference (prototypes/unified-nav-voice/reports/benchmark_1_1_0.md, train + dev).
REFERENCE = {
    "benchmark": _rate(655, 655),
    "routes": _rate(1338, 1338),
    "locate": _rate(553, 553),
    "count": _rate(239, 239),
}


def run(
    splits: Iterable[str] = kit.SPLITS,
    sections: Iterable[str] = SECTIONS,
    progress: Callable[[str], None] | None = None,
    kb: KitBuildings | None = None,
) -> tuple[JSON, list[JSON]]:
    splits, sections = tuple(splits), tuple(sections)
    kb = kb or KitBuildings()
    records: list[JSON] = []
    timing = {section: 0.0 for section in sections}
    began = time.perf_counter()
    for split in splits:
        if "benchmark" in sections:
            t = time.perf_counter()
            for question in kit.examples(split):
                records.append(run_benchmark(kb, question, split))
            timing["benchmark"] += time.perf_counter() - t
            if progress:
                progress(f"[{split}] benchmark done")
        for index, question in enumerate(kit.basic_examples(split)):
            kind = question["kind"]
            section = "routes" if kind in ROUTE_KINDS else kind
            if section not in sections:
                continue
            t = time.perf_counter()
            bid = question["building_id"]
            if kind == "locate":
                records.append(run_locate(kb.building(bid), question, split, kb.source(bid)))
            elif kind == "count":
                records.append(run_count(kb.building(bid), question, split, kb.source(bid)))
            else:
                records.append(run_route(kb, question, split))
            timing[section] += time.perf_counter() - t
            if progress and index % 200 == 0:
                progress(f"[{split}] warm-up {index}")
    timing["total"] = time.perf_counter() - began
    summary = summarise(records)
    report = {
        "label": "app.navigation.NavigationService",
        "splits": list(splits),
        "sections": list(sections),
        "kit": {
            "version": KIT_VERSION,
            "commit": KIT_COMMIT,
            "questions": _display(kit.reference_dir()),
            "buildings": _display(kit.kit_zip()),
        },
        "runtime_s": timing,
        "headline": headline(summary),
        "reference": REFERENCE if set(splits) == set(kit.SPLITS) else {},
        "summary": summary,
        "failures": [r for r in records if not r["ok"]],
    }
    return report, records


def main(argv: list[str] | None = None) -> JSON:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--splits", nargs="+", choices=kit.SPLITS, default=list(kit.SPLITS))
    parser.add_argument("--sections", nargs="+", choices=SECTIONS, default=list(SECTIONS))
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="output prefix (.json/.md)")
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args(argv)
    for check in (kit.questions_available, kit.buildings_available):
        available, reason = check()
        if not available:
            parser.exit(2, f"kit data unavailable: {reason}\n")
    report, _ = run(
        args.splits,
        args.sections,
        (lambda m: print(m, file=sys.stderr, flush=True)) if args.progress else None,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
    )
    out.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    for key, rate in report["headline"].items():
        if key in args.sections:
            ref = report["reference"].get(key)
            seconds = report["runtime_s"].get(key, 0.0)
            note = f" (reference {_fmt(ref)})" if ref else ""
            print(f"{key:<10} {_fmt(rate):>11}{note}  {seconds:.1f} s")
    print(f"total {report['runtime_s']['total']:.1f} s -> {out.with_suffix('.json')}")
    return report


if __name__ == "__main__":
    main()

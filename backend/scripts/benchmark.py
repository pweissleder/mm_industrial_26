"""Graph-only benchmark: ingest every building with the pipeline's graph mode, then answer all
of its questions with a small model and score the replies.

Run from backend/:
    uv run poe benchmark                                  # kit train + dev, every question
    uv run poe benchmark --data <kit>/data/dev --per-kind 2 --no-basic    # quick pilot
    uv run poe benchmark --data <building or split folder> [--questions <file>]
    uv run poe benchmark --data <kit>/data/test --submission ../SUBMISSION/answers.json
    uv run poe benchmark --ingest-only                    # pipeline check only, no API calls
    uv run poe benchmark --render <run folder>            # rebuild report.html of a run
    uv run poe benchmark --rerun <run folder>             # only what that run got wrong

Each building must pass the graph pipeline (raw validation, graph build, graph validation)
without a review, and the cleaned building must keep the source geometry. The exit code is
0 only when every building passed, no question failed to run, and (with --min-score) the
benchmark score reached the threshold. Results, submission CSV, report.md and a static
report.html (open it from disk) go to --out.
"""

import argparse
import asyncio
import csv
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.benchmark.answerer import DEFAULT_MODEL, Answerer
from app.benchmark.data import BuildingSet, Question, discover
from app.benchmark.report import render_html, summarise
from app.benchmark.scoring import Judge, refresh_expected
from app.config import BACKEND_DIR, get_settings
from app.domain.building import BuildingFile
from app.pipeline.runner import PipelineRunner
from app.pipeline.state import MODE_STAGES, ReviewDecision, RunStatus

KIT_DATA = BACKEND_DIR.parent / "problem_statement" / "industrial-agents-hackathon-2026" / "data"
DEFAULT_DATA = [KIT_DATA / "train", KIT_DATA / "dev"]
GRAPH_STAGES = MODE_STAGES["graph"]


@dataclass
class Ingested:
    dataset: str
    location_id: str | None
    status: str
    passed: bool
    seconds: float
    stages: dict[str, str] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    building: BuildingFile | None = None


def _geometry(doc: dict[str, Any]) -> dict[str, Any]:
    """What the graders measure with; the cleanup must leave all of it as the source has it."""
    pick = {
        "floors": ("level", "elevation_m"),
        "spaces": ("floor_id", "type", "polygon"),
        "openings": ("space_ids", "kind", "position", "width_m", "step_free"),
        "vertical_connectors": ("kind", "space_ids", "step_free"),
    }
    return {
        f"{group}/{item['id']}": [item.get(key) for key in keys]
        for group, keys in pick.items()
        for item in doc.get(group, [])
    }


def geometry_drift(raw: dict[str, Any], cleaned: dict[str, Any]) -> list[str]:
    before, after = _geometry(raw), _geometry(cleaned)
    return [
        f"{key} {'removed' if key not in after else 'added' if key not in before else 'changed'}"
        for key in sorted(before.keys() | after.keys())
        if before.get(key) != after.get(key)
    ]


def ingest(runner: PipelineRunner, building: BuildingSet) -> Ingested:
    started = time.perf_counter()
    record = runner.run_sync(
        building.dataset, force=True, datasets_dir=building.directory.parent, mode="graph"
    )
    problems = []
    while record.status == RunStatus.NEEDS_REVIEW and record.pending_review is not None:
        review = record.pending_review
        problems.append(f"review at {review.stage}: {review.message}")
        decision = ReviewDecision(review_id=review.review_id, option="accept_with_warnings")
        record = runner.resume_sync(record.run_id, decision)
    stages = {str(s): str(record.stages[s].status) for s in GRAPH_STAGES}
    problems += [
        f"{s}: {record.stages[s].summary}"
        for s, v in stages.items()
        if v not in ("passed", "skipped")
    ]
    if record.error:
        problems.append(record.error)
    cleaned = None
    if record.status == RunStatus.COMPLETED and record.location_id:
        doc = runner.workspace.locations.read_building(record.location_id)
        cleaned = BuildingFile.model_validate(doc)
        drift = geometry_drift(building.raw, doc)
        problems += [f"geometry drift: {d}" for d in drift[:10]]
    return Ingested(
        dataset=building.dataset,
        location_id=record.location_id,
        status=str(record.status),
        passed=not problems and record.status == RunStatus.COMPLETED,
        seconds=time.perf_counter() - started,
        stages=stages,
        problems=problems,
        building=cleaned,
    )


def failed_ids(run_dir: Path) -> set[str]:
    """Questions an earlier run answered wrong or couldn't run."""
    lines = (run_dir / "results.jsonl").read_text("utf-8").splitlines()
    rows = [json.loads(line) for line in lines if line.strip()]
    return {r["id"] for r in rows if r.get("correct") is False or r.get("error")}


def keep_questions(sets: list[BuildingSet], ids: set[str]) -> list[BuildingSet]:
    for building in sets:
        building.questions = [q for q in building.questions if q.id in ids]
    return [b for b in sets if b.questions]


def sample_per_kind(sets: list[BuildingSet], per_kind: int | None) -> None:
    if per_kind is None:
        return
    seen: Counter[tuple[str, str]] = Counter()
    for building in sets:
        kept = []
        for q in building.questions:
            if seen[(q.group, q.kind)] < per_kind:
                seen[(q.group, q.kind)] += 1
                kept.append(q)
        building.questions = kept


async def answer_all(
    answerer: Answerer,
    jobs: list[tuple[BuildingSet, Ingested, Question]],
    workers: int,
) -> dict[str, dict[str, Any]]:
    semaphore = asyncio.Semaphore(workers)
    judges: dict[str, Judge] = {}
    rows: dict[str, dict[str, Any]] = {}

    async def one(building: BuildingSet, ingested: Ingested, q: Question) -> None:
        if building.dataset not in judges:
            judges[building.dataset] = Judge(building.raw)
        judge = judges[building.dataset]
        expected, refreshed = refresh_expected(building.raw, q)
        error = None
        async with semaphore:
            asked = time.perf_counter()
            try:
                assert ingested.building is not None
                answer = await answerer.ask(ingested.building, q.id, q.text, q.kind)
                reply, raw = answer.text, answer.raw
            except Exception as exc:  # one failure must not sink the run; it scores as wrong
                reply, raw, error = "", "", repr(exc)
            seconds = time.perf_counter() - asked
        score = judge.score(q, expected, reply)
        rows[q.id] = {
            "id": q.id,
            "building_id": q.building_id,
            "group": q.group,
            "kind": q.kind,
            "correct": None if score is None else score.correct,
            "score": None if score is None else round(score.value, 4),
            "detail": score.detail if score else "",
            "refreshed": refreshed,
            "error": error,
            "seconds": round(seconds, 1),
            "question": q.text,
            "answer": expected,
            "reply": reply,
            "raw_reply": raw if raw != reply else None,  # set when the answer line was normalised
        }
        mark = "saved" if score is None else "OK" if score.correct else "BAD"
        cost = answerer.cost()
        print(
            f"[{len(rows):4d}/{len(jobs)}] {q.id:38s} {q.kind:26s} {mark:5s}"
            + (f" ${cost:.2f}" if cost is not None else "")
            + (f"  {error}" if error else ""),
            flush=True,
        )

    await asyncio.gather(*(one(b, i, q) for b, i, q in jobs))
    return rows


def write_submission(path: Path, rows: list[dict[str, Any]]) -> None:
    """The kit's CSV (question_id,answer); a `.json` path gets [{question_id, answer}] instead."""
    if path.suffix == ".json":
        entries = [{"question_id": r["id"], "answer": r["reply"]} for r in rows]
        path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", "utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["question_id", "answer"])
        writer.writerows((r["id"], r["reply"]) for r in rows)


def write_report(
    out: Path,
    args: argparse.Namespace,
    ingested: list[Ingested],
    rows: list[dict[str, Any]],
    cost: float | None,
    seconds: float,
) -> str:
    passed = sum(i.passed for i in ingested)
    errors = [r for r in rows if r["error"]]
    table = summarise(rows)
    lines = [
        f"# Graph-only benchmark ({datetime.now():%Y-%m-%d %H:%M})",
        "",
        f"Data: {', '.join(str(d) for d in args.data)}. Answer model: `{args.model}`. "
        f"Time {seconds:.0f} s" + (f", cost ${cost:.2f}." if cost is not None else "."),
        "",
        "## Pipeline (graph mode: raw validation, graph build, graph validation)",
        "",
        f"**{passed}/{len(ingested)} buildings passed** (completed without review, every stage "
        "passed, source geometry unchanged).",
        "",
    ]
    for item in ingested:
        if not item.passed:
            lines.append(f"- {item.dataset} ({item.status}): " + "; ".join(item.problems[:5]))
    if rows:
        refreshed = sum(bool(r["refreshed"]) for r in rows)
        lines += [
            "",
            "## Answers",
            "",
            f"{len(rows)} questions, {len(errors)} failed to run, {refreshed} expected answers "
            "recomputed with the kit 1.1.0 route model.",
            "",
            "| Group / kind | Correct | Score |",
            "|---|---|---|",
        ]
        for key, entry in table.items():
            share = entry["correct"] / entry["n"]
            mean = entry["score"] / entry["n"]
            bold = "**" if key.endswith("/ALL") else ""
            lines.append(
                f"| {bold}{key}{bold} | {entry['correct']}/{entry['n']} ({share:.0%}) "
                f"| {mean:.3f} |"
            )
        misses = [r for r in rows if r["correct"] is False]
        if misses:
            lines += ["", f"## Misses ({len(misses)})", ""]
            lines += [
                f"- {r['id']} `{r['kind']}`: {r['detail'] or 'wrong'}"
                + (f" ({r['error']})" if r["error"] else "")
                for r in misses[:80]
            ]
    text = "\n".join(lines) + "\n"
    (out / "report.md").write_text(text, encoding="utf-8")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--data",
        type=Path,
        nargs="+",
        default=DEFAULT_DATA,
        help="kit split folders, building folders or a folder of splits (default: kit train+dev)",
    )
    parser.add_argument("--questions", type=Path, help="question file (.jsonl/.csv) to use instead")
    parser.add_argument("--buildings", nargs="+", help="only these building folders")
    parser.add_argument("--no-basic", action="store_true", help="skip questions_basic.jsonl")
    parser.add_argument("--per-kind", type=int, help="at most N questions per kind (pilot runs)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"answer model ({DEFAULT_MODEL})")
    parser.add_argument("--workers", type=int, default=16, help="questions answered in parallel")
    parser.add_argument("--ingest-only", action="store_true", help="pipeline check, no answers")
    parser.add_argument("--min-score", type=float, help="fail below this benchmark accuracy")
    parser.add_argument(
        "--submission",
        type=Path,
        help="also write the submission here (.json: question_id/answer list, else kit CSV)",
    )
    parser.add_argument("--render", type=Path, help="only rebuild report.html of this run folder")
    parser.add_argument(
        "--rerun", type=Path, help="only the questions this earlier run got wrong or couldn't run"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=BACKEND_DIR.parent / "benchmark_results" / f"graph-{datetime.now():%Y%m%d-%H%M}",
    )
    args = parser.parse_args()
    if args.render:
        print(f"Wrote {render_html(args.render.resolve())}")
        return 0

    settings = get_settings()
    if not args.ingest_only and not settings.anthropic_api_key:
        parser.error("ANTHROPIC_API_KEY is not set (backend/.env); use --ingest-only without it")
    sets = discover(args.data, not args.no_basic, args.questions, set(args.buildings or []) or None)
    if not sets:
        parser.error(f"no building.json found under {', '.join(map(str, args.data))}")
    if args.rerun:
        ids = failed_ids(args.rerun.resolve())
        sets = keep_questions(sets, ids)
        print(f"Rerunning {sum(len(b.questions) for b in sets)} of {len(ids)} failed questions")
        if not sets:
            parser.error(f"no failed questions of {args.rerun} found under the given --data")
    sample_per_kind(sets, args.per_kind)
    out: Path = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    settings.workspace_dir = out / "workspace"
    runner = PipelineRunner.from_settings(settings, offline=True)  # graph mode is deterministic

    started = time.perf_counter()
    print(f"Ingesting {len(sets)} buildings (graph mode) into {settings.workspace_dir}")
    ingested = []
    for building in sets:
        item = ingest(runner, building)
        ingested.append(item)
        verdict = "passed" if item.passed else "FAILED: " + "; ".join(item.problems[:3])
        print(f"  {building.dataset:28s} {item.seconds:5.1f}s  {verdict}", flush=True)
    (out / "pipeline.json").write_text(
        json.dumps(
            [{k: v for k, v in i.__dict__.items() if k != "building"} for i in ingested], indent=2
        ),
        encoding="utf-8",
    )

    rows: list[dict[str, Any]] = []
    cost = None
    if not args.ingest_only:
        assert settings.anthropic_api_key is not None
        answerer = Answerer(settings.anthropic_api_key, args.model)
        jobs = [
            (b, i, q)
            for b, i in zip(sets, ingested, strict=True)
            if i.building is not None
            for q in b.questions
        ]
        skipped = sum(
            len(b.questions) for b, i in zip(sets, ingested, strict=True) if not i.building
        )
        print(
            f"\nAnswering {len(jobs)} questions with {args.model} ({args.workers} workers)"
            + (f"; {skipped} skipped (building not ingested)" if skipped else "")
        )
        by_id = asyncio.run(answer_all(answerer, jobs, args.workers))
        rows = [by_id[q.id] for _, _, q in jobs]
        cost = answerer.cost()
        with (out / "results.jsonl").open("w", encoding="utf-8") as handle:
            handle.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        write_submission(out / "submission.csv", rows)
        if args.submission:
            args.submission.parent.mkdir(parents=True, exist_ok=True)
            write_submission(args.submission, rows)

    seconds = time.perf_counter() - started
    report = write_report(out, args, ingested, rows, cost, seconds)
    meta = {
        "finished": f"{datetime.now():%Y-%m-%d %H:%M}",
        "model": args.model,
        "data": [str(d) for d in args.data],
        "cost_usd": cost,
        "seconds": round(seconds, 1),
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    html = render_html(out)
    print("\n" + report)
    benchmark = summarise(rows).get("benchmark/ALL")
    accuracy = benchmark["correct"] / benchmark["n"] if benchmark else None
    failures = []
    if not all(i.passed for i in ingested):
        failures.append("not every building passed the graph pipeline")
    if any(r["error"] for r in rows):
        failures.append("some questions failed to run")
    if args.min_score is not None and (accuracy is None or accuracy < args.min_score):
        failures.append(f"benchmark accuracy {accuracy} below --min-score {args.min_score}")
    print(f"Results in {out}\nOpen {html}")
    for failure in failures:
        print(f"FAIL: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

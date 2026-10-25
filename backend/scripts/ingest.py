"""Run the agentic ingestion pipeline on one or more datasets from the terminal.

Run from backend/:  uv run poe ingest proc-factory-dev-003 [more datasets] [--offline]
                    uv run poe ingest --all --datasets-dir <kit>/data/dev --offline
                    uv run poe ingest proc-factory-dev-003 --graph-only   (benchmark variant)
Without ANTHROPIC_API_KEY (or with --offline) only the deterministic tools run. When a
run pauses for review, you're asked to accept, retry or abort in the terminal.
"""

import argparse
import sys
from pathlib import Path

from app.config import get_settings
from app.ingestion import list_datasets
from app.pipeline.runner import PipelineRunner
from app.pipeline.state import MODE_STAGES, ReviewDecision, RunRecord, RunStatus
from app.tracing import configure_tracing


def _ask(record: RunRecord) -> ReviewDecision:
    review = record.pending_review
    assert review is not None
    print(f"\n== {review.title}\n{review.message}")
    for issue in review.issues[:15]:
        print(f"  - {issue}")
    options = {str(i): o for i, o in enumerate(review.options, 1)}
    for key, option in options.items():
        print(f"  [{key}] {option.label}: {option.description}")
    choice = ""
    while choice not in options:
        choice = input("Choice: ").strip()
    note = input("Note (optional): ").strip() or None
    return ReviewDecision(review_id=review.review_id, option=options[choice].id, note=note)


def _print(record: RunRecord) -> None:
    print(f"{record.dataset}: {record.status} (run {record.run_id})")
    for stage, run in record.stages.items():
        if run.status != "pending" and stage in MODE_STAGES[record.mode]:
            print(f"  {stage:<16} {run.status:<8} x{run.attempts}  {run.summary or ''}")
    if record.error:
        print(f"  error: {record.error}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("datasets", nargs="*")
    parser.add_argument("--all", action="store_true", help="every dataset in --datasets-dir")
    parser.add_argument("--datasets-dir", type=Path, default=None)
    parser.add_argument("--offline", action="store_true", help="deterministic tools only")
    parser.add_argument("--force", action="store_true", help="re-run unchanged inputs")
    parser.add_argument(
        "--graph-only",
        action="store_true",
        help="benchmark variant: deterministic cleanup + relationship graph, no 3D or metadata",
    )
    parser.add_argument(
        "--on-review",
        choices=["ask", "accept_with_warnings", "abort"],
        default="ask",
        help="what to do when a run pauses for review (ask needs a terminal)",
    )
    args = parser.parse_args()

    settings = get_settings()
    if args.datasets_dir:
        settings.datasets_dir = args.datasets_dir.resolve()
    configure_tracing(settings)
    runner = PipelineRunner.from_settings(settings, offline=args.offline)
    datasets = list_datasets(settings.datasets_dir) if args.all else args.datasets
    if not datasets:
        parser.error("name a dataset or pass --all")

    failures = 0
    for dataset in datasets:
        record = runner.run_sync(
            dataset, force=args.force, mode="graph" if args.graph_only else "full"
        )
        while record.status == RunStatus.NEEDS_REVIEW:
            if args.on_review != "ask" or not sys.stdin.isatty():
                review = record.pending_review
                assert review is not None
                option = "abort" if args.on_review == "ask" else args.on_review
                print(f"{dataset}: review at {review.stage} -> {option}: {review.message}")
                decision = ReviewDecision(review_id=review.review_id, option=option)
            else:
                decision = _ask(record)
            record = runner.resume_sync(record.run_id, decision)
        _print(record)
        failures += record.status != RunStatus.COMPLETED
    print(f"\n{len(datasets) - failures}/{len(datasets)} completed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

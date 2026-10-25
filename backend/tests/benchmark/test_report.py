"""The static results page of a benchmark run."""

import json
from pathlib import Path

from app.benchmark.report import render_html, summarise


def _row(qid: str, group: str, kind: str, correct: bool, score: float, **extra: object) -> dict:
    return {
        "id": qid,
        "group": group,
        "kind": kind,
        "correct": correct,
        "score": score,
        "detail": "",
        "error": None,
        "refreshed": None,
        "question": "Q <b>?</b>",
        "answer": {"value": 1},
        "reply": "Answer: 2",
        **extra,
    }


def test_summary_and_page(tmp_path: Path) -> None:
    rows = [
        _row("H1", "benchmark", "errand", True, 1.0),
        _row("H2", "benchmark", "errand", False, 0.0, detail="wrong <number>"),
        _row("Q1", "basic", "route", False, 0.5, detail="12.0 m vs best 6.0 m"),
    ]
    table = summarise(rows)
    assert table["benchmark/ALL"] == {"n": 2, "correct": 1, "score": 1.0}
    assert table["basic/route"]["score"] == 0.5

    (tmp_path / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (tmp_path / "pipeline.json").write_text(
        json.dumps(
            [
                {
                    "dataset": "b1",
                    "passed": True,
                    "seconds": 0.2,
                    "stages": {"graph_build": "passed"},
                },
                {
                    "dataset": "b2",
                    "passed": False,
                    "seconds": 0.1,
                    "stages": {"graph_validate": "failed"},
                    "problems": ["graph_validate: 1 errors"],
                },
            ]
        )
    )
    (tmp_path / "meta.json").write_text(
        json.dumps({"model": "claude-haiku-5-5", "cost_usd": 0.5, "seconds": 90, "data": ["dev"]})
    )

    page = render_html(tmp_path).read_text("utf-8")
    assert "1/2 buildings passed" in page and "graph_validate: 1 errors" in page
    assert "50.0%" in page and "$0.50" in page and "claude-haiku-5-5" in page
    assert "wrong &lt;number&gt;" in page and "<b>?</b>" not in page  # dataset text is escaped
    assert "Misses and errors: 2" in page


def test_page_of_an_ingest_only_run(tmp_path: Path) -> None:
    (tmp_path / "pipeline.json").write_text(json.dumps([{"dataset": "b1", "passed": True}]))
    page = render_html(tmp_path).read_text("utf-8")
    assert "1/1 buildings passed" in page and "Score by question kind" not in page

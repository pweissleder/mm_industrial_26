"""Static results page of a benchmark run (`report.html`), built from the run's files.

Reads `meta.json`, `pipeline.json` and `results.jsonl` from the run folder and writes one
self-contained HTML file (inline CSS, no scripts, no server): open it straight from disk.
`uv run poe benchmark --render <run folder>` rebuilds it for an earlier run.
"""

import json
from collections import defaultdict
from html import escape
from pathlib import Path
from typing import Any

MAX_MISSES = 300


def summarise(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per "group/kind" (and "group/ALL"): scored questions, correct ones, summed score."""
    table: dict[str, dict[str, Any]] = defaultdict(lambda: {"n": 0, "correct": 0, "score": 0.0})
    for row in rows:
        if row["correct"] is None:
            continue
        for key in (f"{row['group']}/{row['kind']}", f"{row['group']}/ALL"):
            entry = table[key]
            entry["n"] += 1
            entry["correct"] += int(row["correct"])
            entry["score"] += row["score"]
    return dict(sorted(table.items()))


def _read_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text("utf-8")) if path.is_file() else default


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def _bar(share: float) -> str:
    tone = "good" if share >= 0.95 else "warn" if share >= 0.8 else "bad"
    return (
        f'<div class="bar"><div class="fill {tone}" style="width:{share * 100:.1f}%"></div></div>'
    )


def _card(label: str, value: str, note: str = "", tone: str = "") -> str:
    return (
        f'<div class="card {tone}"><div class="label">{escape(label)}</div>'
        f'<div class="value">{escape(value)}</div><div class="note">{escape(note)}</div></div>'
    )


def _pre(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1)
    return f"<pre>{escape(text)}</pre>"


CSS = """
:root{--bg:#f7f7f5;--panel:#fff;--text:#1d1d1b;--muted:#6b6b66;--line:#e3e3de;
--good:#2f8f5b;--warn:#c58a1b;--bad:#c2412d;--track:#ecece7;--code:#f1f1ed}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#161615;--panel:#1f1f1d;
--text:#ecece8;--muted:#9a9a93;--line:#33332f;--track:#2c2c29;--code:#262623}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:36px 0 12px}
.meta{color:var(--muted);margin:0 0 24px;overflow-wrap:anywhere}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card .label{color:var(--muted);font-size:13px}.card .value{font-size:28px;font-weight:650}
.card .note{color:var(--muted);font-size:13px}
.card.good .value{color:var(--good)}.card.bad .value{color:var(--bad)}
.card.warn .value{color:var(--warn)}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);
border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:13px;color:var(--muted);font-weight:600}tr.total td{font-weight:650}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap}
.bar{height:8px;background:var(--track);border-radius:4px;min-width:120px;margin-top:7px}
.fill{height:100%;border-radius:4px}.fill.good{background:var(--good)}
.fill.warn{background:var(--warn)}.fill.bad{background:var(--bad)}
.tag{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;
border:1px solid var(--line)}.tag.bad{color:var(--bad)}.tag.good{color:var(--good)}
details{margin-top:4px}summary{cursor:pointer;color:var(--muted)}
pre{background:var(--code);padding:8px 10px;border-radius:6px;white-space:pre-wrap;
word-break:break-word;font-size:12.5px;margin:6px 0}
.scroll{overflow-x:auto}code{font-size:13px}
"""


def render_html(run_dir: Path) -> Path:
    meta: dict[str, Any] = _read_json(run_dir / "meta.json", {})
    pipeline: list[dict[str, Any]] = _read_json(run_dir / "pipeline.json", [])
    rows = _read_rows(run_dir / "results.jsonl")
    table = summarise(rows)

    passed = sum(bool(p.get("passed")) for p in pipeline)
    errors = sum(bool(r.get("error")) for r in rows)
    refreshed = sum(bool(r.get("refreshed")) for r in rows)
    bench, basic = table.get("benchmark/ALL"), table.get("basic/ALL")
    cost, seconds = meta.get("cost_usd"), meta.get("seconds")

    cards = [
        _card(
            "Pipeline (graph mode)",
            f"{passed}/{len(pipeline)}",
            "buildings passed",
            "good" if pipeline and passed == len(pipeline) else "bad",
        )
    ]
    if bench:
        share = bench["correct"] / bench["n"]
        tone = "good" if share >= 0.95 else "warn" if share >= 0.8 else "bad"
        cards.append(
            _card(
                "Benchmark questions",
                f"{share:.1%}",
                f"{bench['correct']}/{bench['n']} correct",
                tone,
            )
        )
    if basic:
        mean = basic["score"] / basic["n"]
        tone = "good" if mean >= 0.95 else "warn" if mean >= 0.8 else "bad"
        cards.append(
            _card(
                "Warm-up questions",
                f"{mean:.3f}",
                f"mean score, {basic['correct']}/{basic['n']} full marks",
                tone,
            )
        )
    if rows:
        cards.append(
            _card("Questions", str(len(rows)), f"{errors} failed to run", "bad" if errors else "")
        )
    if cost is not None or seconds is not None:
        value = f"${cost:.2f}" if cost is not None else "–"
        note = f"{seconds / 60:.1f} min" if seconds is not None else ""
        cards.append(_card("Cost", value, note))

    kind_rows = []
    for key, entry in table.items():
        share = entry["correct"] / entry["n"]
        total = key.endswith("/ALL")
        kind_rows.append(
            f'<tr class="{"total" if total else ""}"><td>{escape(key)}</td>'
            f'<td class="num">{entry["correct"]}/{entry["n"]}</td>'
            f'<td class="num">{share:.1%}{_bar(share)}</td>'
            f'<td class="num">{entry["score"] / entry["n"]:.3f}</td></tr>'
        )

    failed_rows: list[str] = []
    passed_rows: list[str] = []
    for item in sorted(pipeline, key=lambda p: p.get("dataset", "")):
        ok = bool(item.get("passed"))
        statuses = item.get("stages", {})
        stages = (
            f"{len(statuses)}/{len(statuses)} stages passed"
            if statuses and all(v == "passed" for v in statuses.values())
            else " · ".join(f"{s}: {v}" for s, v in statuses.items())
        )
        problems = "".join(f"<div>{escape(p)}</div>" for p in item.get("problems", [])[:8])
        (passed_rows if ok else failed_rows).append(
            f"<tr><td><code>{escape(item.get('dataset', ''))}</code></td>"
            f'<td><span class="tag {"good" if ok else "bad"}">'
            f"{'passed' if ok else 'failed'}</span></td>"
            f'<td class="num">{item.get("seconds", 0):.1f} s</td>'
            f"<td>{escape(stages)}{problems}</td></tr>"
        )

    misses = [r for r in rows if r.get("correct") is False or r.get("error")]
    miss_rows = []
    for r in misses[:MAX_MISSES]:
        note = r.get("error") or r.get("detail") or "wrong answer"
        score = r.get("score")
        miss_rows.append(
            f"<tr><td><code>{escape(r['id'])}</code></td><td>{escape(r['kind'])}</td>"
            f'<td class="num">{"–" if score is None else f"{score:.2f}"}</td>'
            f"<td>{escape(note)}<details><summary>question, expected answer, reply</summary>"
            f"{_pre(r.get('question', ''))}{_pre(r.get('answer'))}{_pre(r.get('reply', ''))}"
            "</details></td></tr>"
        )

    data = ", ".join(meta.get("data", [])) or "unknown data"
    parts = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        '<meta name=viewport content="width=device-width,initial-scale=1">',
        f"<title>Benchmark results</title><style>{CSS}</style></head><body><main>",
        "<h1>Graph-only benchmark</h1>",
        f'<p class="meta">{escape(meta.get("finished", ""))} · '
        + (f"model <code>{escape(meta.get('model', '?'))}</code> · " if rows else "ingest only · ")
        + f"{escape(data)}</p>",
        f'<div class="cards">{"".join(cards)}</div>',
    ]
    if kind_rows:
        parts += [
            "<h2>Score by question kind</h2>",
            '<div class="scroll"><table><tr><th>Group / kind</th><th>Correct</th>'
            "<th>Accuracy</th><th>Mean score</th></tr>",
            *kind_rows,
            "</table></div>",
            f'<p class="meta">Benchmark questions: official scorer. Warm-up routes: organizer '
            f"rules (valid route, best length ÷ our length, full marks within 1 %). "
            f"{refreshed} expected answers recomputed with the kit 1.1.0 route model.</p>",
        ]
    header = (
        '<div class="scroll"><table><tr><th>Building</th><th>Result</th><th>Time</th>'
        "<th>Stages and problems</th></tr>"
    )
    parts.append(f"<h2>Pipeline: {passed}/{len(pipeline)} buildings passed</h2>")
    parts.append(
        '<p class="meta">Graph mode: raw validation with deterministic cleanup, graph build, '
        "graph validation. Passed = completed without review, every stage passed, floors, "
        "outlines, openings and connectors identical to the source.</p>"
    )
    if failed_rows:
        parts += [header, *failed_rows, "</table></div>"]
    if passed_rows:
        parts += [
            f"<details><summary>{len(passed_rows)} buildings passed (show)</summary>",
            header,
            *passed_rows,
            "</table></div></details>",
        ]
    if misses:
        shown = f" (first {MAX_MISSES})" if len(misses) > MAX_MISSES else ""
        parts += [
            f"<h2>Misses and errors: {len(misses)}{shown}</h2>",
            '<div class="scroll"><table><tr><th>Question</th><th>Kind</th><th>Score</th>'
            "<th>Why</th></tr>",
            *miss_rows,
            "</table></div>",
        ]
    parts.append("</main></body></html>\n")
    path = run_dir / "report.html"
    path.write_text("".join(parts), encoding="utf-8")
    return path

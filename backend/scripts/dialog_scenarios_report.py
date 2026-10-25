"""Run the offline dialog/state/tool scenarios and write their report.

    .venv/Scripts/python scripts/dialog_scenarios_report.py

Runs tests/assistant/test_dialog_scenarios.py with pytest (--junitxml, no cache) and writes
docs/night/tests/dialog_scenarios_offline.md and .json: one row per scenario with its acceptance-
matrix row, what it checks and the result (passed / xfail = known defect / failed / skipped).
These scenarios script the model's tool calls; they test state/tool/guard logic, not language
understanding. Exit code: pytest's (0 = all passed or xfailed as expected).
"""

import importlib
import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# Scenario metadata (id, matrix row, what is checked, known defect) from the test table itself;
# imported at runtime so `mypy scripts` does not type-check the test package.
SCENARIOS: list[Any] = importlib.import_module("tests.assistant.test_dialog_scenarios").SCENARIOS

TEST_FILE = "tests/assistant/test_dialog_scenarios.py"
OUT_DIR = BACKEND.parent / "docs" / "night" / "tests"
PREFIX = "test_dialog_scenario["

HEADER = """# Offline dialog / state / tool scenarios

**What this is:** {count} defined scenarios that drive the REAL assistant (session state, UI
sync, tool handlers, step-free/width guards, proposals/closures) on the real NavigationService
and real buildings. Claude is replaced by a scripted client that returns the tool calls a model
would make, turn by turn, then a final text.

**What this is NOT:** a language-understanding test. Whether Claude chooses these tool calls for a
given sentence is not tested here (that is covered by a few live dialogs). No network, no API key,
no constant-text mock.

Generated {generated} by `backend/scripts/dialog_scenarios_report.py` from
`backend/{test_file}` (pytest exit code {exit_code}).

Result legend: `passed`; `xfail` = the scenario documents a genuine defect of the current code
(strict xfail: it fails today, the reason is the defect); `FAILED` = regression; `skipped` =
building data missing.
"""


def run_pytest(junit: Path) -> int:
    command = [
        sys.executable,
        "-m",
        "pytest",
        TEST_FILE,
        "-q",
        "-p",
        "no:cacheprovider",
        f"--junitxml={junit}",
    ]
    return subprocess.run(command, cwd=BACKEND, check=False).returncode


def parse_junit(junit: Path) -> dict[str, dict[str, str]]:
    results: dict[str, dict[str, str]] = {}
    for case in ET.parse(junit).getroot().iter("testcase"):
        name = case.get("name", "")
        if not name.startswith(PREFIX):
            continue
        scenario_id = name[len(PREFIX) : -1]
        outcome, detail = "passed", ""
        for child in case:
            message = child.get("message", "")
            if child.tag in {"failure", "error"}:
                outcome, detail = "FAILED", message
            elif child.tag == "skipped":
                xfail = child.get("type") == "pytest.xfail"
                outcome, detail = ("xfail" if xfail else "skipped"), message
        results[scenario_id] = {"result": outcome, "detail": detail, "time_s": case.get("time", "")}
    return results


def cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def write_reports(results: dict[str, dict[str, str]], exit_code: int) -> tuple[Path, Path]:
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    rows: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        found = results.get(scenario.id, {"result": "not run", "detail": "", "time_s": ""})
        rows.append(
            {
                "id": scenario.id,
                "matrix_row": scenario.row,
                "checks": scenario.checks,
                "turns": len(scenario.turns),
                "result": found["result"],
                "defect": scenario.xfail,
                "fixed_defect": scenario.fixed,
                "detail": found["detail"] if found["result"] in {"FAILED", "skipped"} else "",
                "time_s": found["time_s"],
            }
        )
    per_row: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        per_row[row["matrix_row"]][row["result"]] += 1
    totals = Counter(row["result"] for row in rows)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUT_DIR / "dialog_scenarios_offline.json"
    json_path.write_text(
        json.dumps(
            {
                "generated": generated,
                "kind": "offline scripted tool calls: state/tool/guard logic, not NLU",
                "test_file": f"backend/{TEST_FILE}",
                "pytest_exit_code": exit_code,
                "totals": dict(totals),
                "per_matrix_row": {k: dict(v) for k, v in sorted(per_row.items())},
                "scenarios": rows,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    lines = [
        HEADER.format(
            count=len(rows), generated=generated, test_file=TEST_FILE, exit_code=exit_code
        ),
        "## Totals\n",
        ", ".join(f"{k}: {v}" for k, v in sorted(totals.items())) + f" (of {len(rows)})\n",
        "| Matrix row | " + " | ".join(["passed", "xfail", "FAILED", "skipped"]) + " |",
        "|---|---|---|---|---|",
    ]
    for key, counts in sorted(per_row.items()):
        values = " | ".join(str(counts.get(k, 0)) for k in ("passed", "xfail", "FAILED", "skipped"))
        lines.append(f"| {key} | {values} |")
    lines += [
        "",
        "## Scenarios\n",
        "| ID | Matrix row | What is checked | Result |",
        "|---|---|---|---|",
    ]
    for row in rows:
        result = row["result"] + (f" ({cell(row['detail'])[:200]})" if row["detail"] else "")
        lines.append(f"| {row['id']} | {row['matrix_row']} | {cell(row['checks'])} | {result} |")
    defects = [row for row in rows if row["defect"]]
    lines += ["", "## Known defects (strict xfail)\n"]
    lines += [f"- **{row['id']}** ({row['result']}): {cell(row['defect'])}" for row in defects]
    if not defects:
        lines.append("None open.")
    fixed = [row for row in rows if row["fixed_defect"]]
    lines += ["", "## Defects these scenarios found, fixed since (now regression tests)\n"]
    lines += [f"- **{row['id']}** ({row['result']}): {cell(row['fixed_defect'])}" for row in fixed]
    lines += [
        "",
        "Matrix rows: C01-C09, S01-S03, S05, H01-H03 from docs/night/ACCEPTANCE_MATRIX.csv; "
        "INV-n = invariant n of docs/night/NIGHT_TASK.md section 'Invarianten'.",
        "",
    ]
    md_path = OUT_DIR / "dialog_scenarios_offline.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path, json_path


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "dialog_scenarios.xml"
        exit_code = run_pytest(junit)
        if not junit.is_file():
            print("pytest produced no junit report", file=sys.stderr)
            return exit_code or 1
        results = parse_junit(junit)
    md_path, json_path = write_reports(results, exit_code)
    print(f"wrote {md_path}\nwrote {json_path}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

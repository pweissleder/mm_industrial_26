"""Where the benchmark input comes from.

`--data` may point at a kit split (`data/dev`: one folder per building), at one building
folder, or at a folder of splits (`data`: train, dev, test). Each building brings its own
`questions.jsonl` (scored benchmark questions) and, optionally, `questions_basic.jsonl`
(warm-up questions). A separate question file (`.jsonl`, or `.csv` like the kit's
`test/questions.csv`) replaces the per-building files; its rows name their `building_id`.
"""

import csv
import json
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

BENCHMARK_FILE = "questions.jsonl"
BASIC_FILE = "questions_basic.jsonl"
SPLITS = ("train", "dev", "test")


@dataclass(frozen=True)
class Question:
    id: str
    building_id: str
    kind: str
    text: str
    group: str  # "benchmark" (questions.jsonl) or "basic" (warm-up)
    answer: dict[str, Any] | None = None
    inputs: dict[str, Any] | None = None


@dataclass
class BuildingSet:
    dataset: str  # folder name, the pipeline's dataset id
    directory: Path
    questions: list[Question] = field(default_factory=list)

    @cached_property
    def raw(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads((self.directory / "building.json").read_text("utf-8"))
        return data


def _rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".csv":
        with path.open(encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    lines = path.read_text("utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _question(row: dict[str, Any], group: str) -> Question:
    return Question(
        id=row.get("id") or row["question_id"],
        building_id=row["building_id"],
        kind=row.get("kind", ""),
        text=row["question"],
        group=group,
        answer=row.get("answer"),
        inputs=row.get("inputs"),
    )


def building_dirs(data: Path) -> list[Path]:
    if (data / "building.json").is_file():
        return [data]
    direct = sorted(p.parent for p in data.glob("*/building.json"))
    if direct:
        return direct
    return sorted(p.parent for split in SPLITS for p in (data / split).glob("*/building.json"))


def discover(
    data: list[Path],
    include_basic: bool = True,
    questions_file: Path | None = None,
    only: set[str] | None = None,
) -> list[BuildingSet]:
    sets = [
        BuildingSet(dataset=d.name, directory=d)
        for root in data
        for d in building_dirs(root)
        if only is None or d.name in only
    ]
    by_id = {s.dataset: s for s in sets}
    if questions_file is not None:
        for row in _rows(questions_file):
            target = by_id.get(row["building_id"])
            if target is not None:
                target.questions.append(_question(row, "benchmark"))
        return sets
    for s in sets:
        files = [(BENCHMARK_FILE, "benchmark")] + ([(BASIC_FILE, "basic")] if include_basic else [])
        for name, group in files:
            path = s.directory / name
            if path.is_file():
                s.questions += [_question(row, group) for row in _rows(path)]
    return sets

"""Finding buildings and questions for the benchmark."""

import json
from pathlib import Path

from app.benchmark.data import discover
from tests.pipeline.factories import make_building_dict


def _building(root: Path, name: str, basic: bool = True) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "building.json").write_text(json.dumps(make_building_dict(building_id=name)))
    question = {"id": f"{name}-H01", "building_id": name, "kind": "errand", "question": "?"}
    (folder / "questions.jsonl").write_text(json.dumps(question) + "\n")
    if basic:
        warmup = {"id": f"{name}-Q01", "building_id": name, "kind": "locate", "question": "?"}
        (folder / "questions_basic.jsonl").write_text(json.dumps(warmup) + "\n")
    return folder


def test_split_folder_single_building_and_folder_of_splits(tmp_path: Path) -> None:
    _building(tmp_path / "data" / "dev", "b1")
    _building(tmp_path / "data" / "train", "b2", basic=False)

    split = discover([tmp_path / "data" / "dev"])
    assert [s.dataset for s in split] == ["b1"]
    assert [(q.id, q.group) for q in split[0].questions] == [
        ("b1-H01", "benchmark"),
        ("b1-Q01", "basic"),
    ]
    assert [s.dataset for s in discover([tmp_path / "data"])] == ["b1", "b2"]
    one = discover([tmp_path / "data" / "dev" / "b1"], include_basic=False)
    assert [q.group for q in one[0].questions] == ["benchmark"]
    assert discover([tmp_path / "data"], only={"b2"})[0].dataset == "b2"


def test_question_file_replaces_the_building_files(tmp_path: Path) -> None:
    _building(tmp_path / "test", "b1")
    csv_file = tmp_path / "questions.csv"
    csv_file.write_text(
        'question_id,building_id,kind,question\nX1,b1,largest_room,"Which room, and how big?"\n'
        "X2,unknown,errand,ignored\n",
        encoding="utf-8",
    )

    [building] = discover([tmp_path / "test"], questions_file=csv_file)
    [question] = building.questions
    assert (question.id, question.kind, question.text) == (
        "X1",
        "largest_room",
        "Which room, and how big?",
    )
    assert question.answer is None

"""`PipelineRunner.dataset_dir` accepts folders the reconstruct stage can build from."""

from pathlib import Path

import pytest

from app.config import Settings
from app.ingestion import DatasetNotFoundError
from app.pipeline.runner import PipelineRunner


def test_dataset_dir_accepts_plan_only_folder(settings: Settings, tmp_path: Path) -> None:
    (tmp_path / "plans").mkdir()
    (tmp_path / "plans" / "floor_0.svg").write_text("<svg/>")
    (tmp_path / "empty").mkdir()
    runner = PipelineRunner.from_settings(settings, offline=True)

    assert runner.dataset_dir("plans", tmp_path) == tmp_path / "plans"
    with pytest.raises(DatasetNotFoundError):
        runner.dataset_dir("empty", tmp_path)
    with pytest.raises(DatasetNotFoundError):
        runner.dataset_dir("missing", tmp_path)

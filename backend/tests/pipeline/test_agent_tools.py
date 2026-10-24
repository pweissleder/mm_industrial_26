"""Agent tools are strict: closed schemas, and the typed patch tool behaves like RFC 6902."""

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.config import BACKEND_DIR, Settings
from app.pipeline.agents import raw_validation
from app.pipeline.agents.base import AgentModels, StageContext, agent_tool
from app.pipeline.agents.variant_creator import SpecArg
from app.pipeline.runner import PipelineRunner

DATASET = "proc-factory-dev-003"


def _objects(schema: Any) -> Iterator[dict[str, Any]]:
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            yield schema
        for value in schema.values():
            yield from _objects(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _objects(value)


def _assert_strict(tool: Any) -> None:
    definition = tool.to_dict()
    assert definition["strict"] is True, definition["name"]
    for obj in _objects(definition["input_schema"]):
        assert obj.get("additionalProperties") is False, (definition["name"], obj)


@pytest.fixture
def session(settings: Settings, tmp_path: Path) -> Any:
    shutil.copytree(BACKEND_DIR / "data" / "datasets" / DATASET, tmp_path / "ds" / DATASET)
    settings.datasets_dir = tmp_path / "ds"
    settings.workspace_dir = tmp_path / "ws"
    runner = PipelineRunner.from_settings(settings, offline=True)
    runner.run_sync(DATASET)
    ctx = StageContext(
        client=None,
        models=AgentModels("o", "v", "w", "s"),
        workspace=runner.workspace,
        run_id="t",
        location_id=DATASET,
        dataset_dir=settings.datasets_dir / DATASET,
    )
    return raw_validation._Session(ctx)


def test_raw_validation_tools_are_strict(session: Any) -> None:
    for tool in raw_validation._tools(session):
        _assert_strict(tool)


def test_spec_tool_is_strict_but_keeps_validating_bounds() -> None:
    @agent_tool
    def save_spec(spec: SpecArg) -> str:
        """Save a spec.

        Args:
            spec: The asset spec.
        """
        return spec.type

    _assert_strict(save_spec)
    box = {"kind": "box", "center": [0, 0, 0.5], "size": [1, 1, 1]}
    ok = {"type": "x", "category": "c", "default_height_m": 1, "primitives": [box]}
    assert save_spec.call({"spec": ok}) == "x"
    # Bounds strict mode can't express (le=12) are still enforced locally.
    with pytest.raises(ValueError):
        save_spec.call({"spec": {**ok, "default_height_m": 50}})


def _data(result: str) -> Any:
    """Tool results that carry dataset content are delimited as untrusted data."""
    assert result.startswith("<dataset_content>\n") and result.endswith("\n</dataset_content>")
    return json.loads(result.removeprefix("<dataset_content>").removesuffix("</dataset_content>"))


def _tool(session: Any, name: str) -> Any:
    return next(t for t in raw_validation._tools(session) if t.name == name)


def test_typed_patch_ops_apply_like_rfc6902(session: Any) -> None:
    patch = _tool(session, "apply_patch_ops")
    op = {
        "op": "replace",
        "path": "/assets/0/footprint/rotation_deg",
        "value_json": "45",
        "from_path": None,
    }
    result = _data(patch.call({"ops": [op], "reason": "r", "issue_ids": [], "issue_class": "t"}))
    assert result["ok"] is True
    assert session.doc["assets"][0]["footprint"]["rotation_deg"] == 45


@pytest.mark.parametrize(
    ("op", "error"),
    [
        (
            {"op": "replace", "path": "/assets/0/name", "value_json": "{bad", "from_path": None},
            "not valid JSON",
        ),
        (
            {"op": "add", "path": "/assets/0/x", "value_json": None, "from_path": None},
            "needs value_json",
        ),
        (
            {"op": "move", "path": "/assets/0/x", "value_json": None, "from_path": None},
            "needs from_path",
        ),
    ],
)
def test_malformed_patch_ops_are_explained(session: Any, op: dict[str, Any], error: str) -> None:
    patch = _tool(session, "apply_patch_ops")
    result = _data(patch.call({"ops": [op], "reason": "r", "issue_ids": [], "issue_class": "t"}))
    assert result["ok"] is False and error in result["errors"][0]

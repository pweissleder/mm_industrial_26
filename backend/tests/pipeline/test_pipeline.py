"""End-to-end pipeline runs (offline: deterministic tools only) and orchestrator policy."""

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import BACKEND_DIR, Settings
from app.main import create_app
from app.pipeline import graph as pipeline_graph
from app.pipeline import orchestrator
from app.pipeline.runner import PipelineRunner, RunStateError
from app.pipeline.state import (
    Decision,
    Deviation,
    ReviewDecision,
    RunRecord,
    RunStatus,
    StageReport,
)
from tests.pipeline.factories import asset, make_building_dict, opening

DATASET = "proc-factory-dev-003"
SAMPLE = BACKEND_DIR / "data" / "datasets" / DATASET


@pytest.fixture
def pipeline_settings(settings: Settings, tmp_path: Path) -> Settings:
    datasets = tmp_path / "datasets"
    shutil.copytree(SAMPLE, datasets / DATASET)
    settings.datasets_dir = datasets
    settings.workspace_dir = tmp_path / "workspace"
    return settings


@pytest.fixture
def runner(pipeline_settings: Settings) -> PipelineRunner:
    return PipelineRunner.from_settings(pipeline_settings, offline=True)


def test_offline_run_builds_location(runner: PipelineRunner) -> None:
    record = runner.run_sync(DATASET)

    assert record.status == RunStatus.COMPLETED, record
    assert record.location_id == DATASET
    assert record.stages["raw_validation"].status == "passed"
    assert record.stages["graph_build"].status == "passed"
    assert record.stages["graph_validate"].status == "passed"
    assert record.stages["model_validate"].status == "passed"
    assert record.stages["summarize"].status == "passed"

    store = runner.workspace.locations
    location = store.get(DATASET)
    assert location is not None and location.metadata is not None
    assert location.pillars["assets_3d"].status == "done"
    assert location.pillars["graph"].status == "done"
    building = store.read_building(DATASET)
    assert set(location.metadata.spaces) == {s["id"] for s in building["spaces"]}
    assert location.metadata.summary
    assert store.path(DATASET, "3d/model.glb").stat().st_size > 0
    assert list(store.path(DATASET, "3d/previews").glob("plan_floor_*.png"))
    # The raw copy is untouched; every change to the cleaned copy is in the patch log.
    assert store.read_building(DATASET, raw=True) == json.loads(
        (SAMPLE / "building.json").read_bytes()
    )


def test_unchanged_input_is_skipped_unless_forced(runner: PipelineRunner) -> None:
    runner.run_sync(DATASET)

    again = runner.run_sync(DATASET)
    assert again.status == RunStatus.COMPLETED
    assert again.stages["raw_validation"].status == "pending"

    forced = runner.run_sync(DATASET, force=True)
    assert forced.stages["raw_validation"].status == "passed"


def test_graph_mode_runs_cleanup_and_graph_only(runner: PipelineRunner) -> None:
    record = runner.run_sync(DATASET, mode="graph")

    assert record.status == RunStatus.COMPLETED, record
    assert record.mode == "graph"
    for stage in ("raw_validation", "graph_build", "graph_validate"):
        assert record.stages[stage].status == "passed"
    for stage in ("floorplan", "equipment", "model_validate", "summarize", "suggest"):
        assert record.stages[stage].status == "skipped"
    assert not record.decisions or all(d["action"] == "proceed" for d in record.decisions)

    store = runner.workspace.locations
    location = store.get(DATASET)
    assert location is not None and location.metadata is None
    assert location.pillars["validation"].status == "done"
    assert location.pillars["graph"].status == "done"
    assert location.pillars["assets_3d"].status == "skipped"
    assert location.pillars["metadata"].status == "skipped"
    assert "model" not in location.artifacts
    assert not store.path(DATASET, "3d/model.glb").exists()
    assert store.path(DATASET, "graph/room_graph.json").is_file()


def test_graph_mode_keeps_the_source_geometry(runner: PipelineRunner) -> None:
    runner.run_sync(DATASET, mode="graph")

    raw = json.loads((SAMPLE / "building.json").read_bytes())
    cleaned = runner.workspace.locations.read_building(DATASET)
    for group in ("floors", "spaces", "openings", "vertical_connectors"):
        assert cleaned[group] == raw[group], group


@pytest.mark.parametrize(("mode", "openings_left"), [("graph", 3), ("full", 2)])
def test_only_graph_mode_keeps_a_duplicate_entrance(
    pipeline_settings: Settings, mode: str, openings_left: int
) -> None:
    building = make_building_dict(
        building_id="dup-001",
        openings=[
            opening("O1", ["S1", "S2"]),
            opening("E1", ["S1", "outside"], (0.0, 5.0), kind="entrance"),
            opening("E2", ["S1", "outside"], (0.0, 5.0), kind="entrance"),
        ],
        assets=[asset("A1", rotation_deg=450.0)],
    )
    folder = pipeline_settings.datasets_dir / "dup-001"
    folder.mkdir()
    (folder / "building.json").write_text(json.dumps(building))
    runner = PipelineRunner.from_settings(pipeline_settings, offline=True)

    record = runner.run_sync("dup-001", mode=mode)  # type: ignore[arg-type]

    assert record.stages["raw_validation"].status == "passed"
    cleaned = runner.workspace.locations.read_building("dup-001")
    assert len(cleaned["openings"]) == openings_left
    assert cleaned["assets"][0]["footprint"]["rotation_deg"] == 90.0  # safe fix in both modes


def test_graph_mode_is_skipped_after_a_run_and_full_mode_still_builds(
    runner: PipelineRunner,
) -> None:
    runner.run_sync(DATASET, mode="graph")

    again = runner.run_sync(DATASET, mode="graph")
    assert again.stages["raw_validation"].status == "pending"
    assert again.decisions[-1]["action"] == "skip"

    full = runner.run_sync(DATASET)
    assert full.stages["floorplan"].status == "passed"
    assert runner.workspace.locations.path(DATASET, "3d/model.glb").is_file()


def _failing_floorplan(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(ctx: Any, state: Any) -> StageReport:
        return StageReport(
            stage="floorplan",
            status="failed",
            summary="walls missing",
            deviations=[Deviation(stage="floorplan", kind="test", message="no walls")],
        )

    monkeypatch.setitem(pipeline_graph.STAGES, "floorplan", fail)


def test_failed_stage_pauses_for_review_and_resumes(
    monkeypatch: pytest.MonkeyPatch, pipeline_settings: Settings
) -> None:
    _failing_floorplan(monkeypatch)
    runner = PipelineRunner.from_settings(pipeline_settings, offline=True)

    paused = runner.run_sync(DATASET)
    assert paused.status == RunStatus.NEEDS_REVIEW
    review = paused.pending_review
    assert review is not None and review.stage == "floorplan"
    assert "no walls" in review.issues
    assert {o.id for o in review.options} == {"accept_with_warnings", "retry", "abort"}

    done = runner.resume_sync(
        paused.run_id, ReviewDecision(review_id=review.review_id, option="accept_with_warnings")
    )
    assert done.status == RunStatus.COMPLETED
    location = runner.workspace.locations.get(DATASET)
    assert location is not None and location.pillars["assets_3d"].status == "degraded"
    assert any(d.get("review") for d in done.decisions)


def test_review_abort_stops_run(
    monkeypatch: pytest.MonkeyPatch, pipeline_settings: Settings
) -> None:
    _failing_floorplan(monkeypatch)
    runner = PipelineRunner.from_settings(pipeline_settings, offline=True)
    paused = runner.run_sync(DATASET)
    assert paused.pending_review is not None

    aborted = runner.resume_sync(
        paused.run_id, ReviewDecision(review_id=paused.pending_review.review_id, option="abort")
    )
    assert aborted.status == RunStatus.ABORTED
    assert aborted.stages["summarize"].status == "pending"


@pytest.fixture
def client(pipeline_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(pipeline_settings)) as c:
        yield c


def test_api_runs_pipeline_and_serves_location(client: TestClient) -> None:
    started = client.post("/api/pipeline-runs", json={"dataset": DATASET})
    assert started.status_code == 202
    run_id = started.json()["run_id"]

    # TestClient runs the app's loop in a thread; wait for the background run there.
    client.portal.call(client.app.state.pipeline_runner.wait)  # type: ignore[union-attr,attr-defined]

    run = client.get(f"/api/pipeline-runs/{run_id}").json()
    assert run["status"] == "completed", run
    assert [loc["id"] for loc in client.get("/api/locations").json()] == [DATASET]
    location = client.get(f"/api/locations/{DATASET}").json()
    assert location["metadata"]["spaces"]
    model = client.get(f"/api/locations/{DATASET}/model.glb")
    assert model.status_code == 200 and model.content[:4] == b"glTF"
    # Finalize also publishes the cleaned building to the building repository.
    assert client.get(f"/api/buildings/{DATASET}").status_code == 200

    assert [r["run_id"] for r in client.get(f"/api/pipeline-runs?dataset={DATASET}").json()] == [
        run_id
    ]
    assert client.get("/api/pipeline-runs?dataset=other").json() == []
    assert client.get("/api/locations").json()[0]["has_model"] is True
    building = client.get(f"/api/locations/{DATASET}/building")
    assert building.status_code == 200 and building.json()["building"]["id"] == DATASET
    preview = client.get(f"/api/locations/{DATASET}/files/3d/previews/plan_floor_0.png")
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/png"


def test_api_starts_a_graph_only_run(client: TestClient) -> None:
    started = client.post("/api/pipeline-runs", json={"dataset": DATASET, "mode": "graph"})
    assert started.status_code == 202 and started.json()["mode"] == "graph"

    client.portal.call(client.app.state.pipeline_runner.wait)  # type: ignore[union-attr,attr-defined]

    run = client.get(f"/api/pipeline-runs/{started.json()['run_id']}").json()
    assert run["status"] == "completed", run
    assert run["stages"]["floorplan"]["status"] == "skipped"
    assert client.get("/api/locations").json()[0]["has_model"] is False
    assert client.get(f"/api/buildings/{DATASET}").status_code == 200
    bad = client.post("/api/pipeline-runs", json={"dataset": DATASET, "mode": "3d"})
    assert bad.status_code == 422


def test_api_location_files_stay_inside_the_location(client: TestClient) -> None:
    client.post("/api/pipeline-runs", json={"dataset": DATASET})
    client.portal.call(client.app.state.pipeline_runner.wait)  # type: ignore[union-attr,attr-defined]

    base = f"/api/locations/{DATASET}/files"
    assert client.get(f"{base}/location.json").status_code == 404  # not an artifact type
    assert client.get(f"{base}/..%2F..%2Fruns%2Fx.png").status_code == 404
    assert client.get(f"{base}/3d/previews/missing.png").status_code == 404


def test_api_errors(client: TestClient) -> None:
    assert client.post("/api/pipeline-runs", json={"dataset": "../etc"}).status_code == 404
    assert client.get("/api/pipeline-runs/nope").status_code == 404
    assert client.get("/api/locations/nope").status_code == 404
    assert client.get("/api/locations/nope/building").status_code == 404
    resume = client.post(
        "/api/pipeline-runs/nope/resume", json={"review_id": "x", "option": "abort"}
    )
    assert resume.status_code == 404


# --- orchestrator policy -------------------------------------------------------------


def _report(stage: str, status: str, blamed: list[str] | None = None) -> StageReport:
    return StageReport.model_validate(
        {
            "stage": stage,
            "status": status,
            "summary": "s",
            "deviations": [{"stage": b, "kind": "k", "message": "m"} for b in blamed or []],
        }
    )


def test_retry_targets_the_blamed_stage() -> None:
    report = _report("model_validate", "failed", ["equipment", "variant_creator"])
    assert orchestrator.retry_target(report) == "equipment"
    decision = orchestrator.default_decision(report, {"equipment": 1}, max_retries=2)
    assert decision.action == "retry" and decision.target_stage == "equipment"
    exhausted = orchestrator.default_decision(report, {"equipment": 3}, max_retries=2)
    assert exhausted.action == "ask_human"


class _StubClient:
    """Answers every structured call with a fixed decision."""

    def __init__(self, decision: Decision) -> None:
        message = SimpleNamespace(
            parsed_output=decision, stop_reason="end_turn", stop_details=None, usage=None
        )
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=lambda **_: message))


def test_orchestrator_cannot_wave_through_a_failure() -> None:
    client: Any = _StubClient(Decision(action="proceed", reason="looks fine"))
    decision, _ = orchestrator.decide(client, "m", _report("raw_validation", "failed"), {}, 2)
    assert decision.action == "retry"


def test_orchestrator_retry_target_is_constrained() -> None:
    client: Any = _StubClient(Decision(action="retry", target_stage="summarize", reason="r"))
    report = _report("model_validate", "failed", ["floorplan"])
    decision, _ = orchestrator.decide(client, "m", report, {"floorplan": 1}, 2)
    assert decision.action == "retry" and decision.target_stage == "floorplan"


def test_routine_passes_skip_the_llm() -> None:
    client: Any = _StubClient(Decision(action="abort", reason="should not be asked"))
    decision, _ = orchestrator.decide(client, "m", _report("equipment", "passed"), {}, 2)
    assert decision.action == "proceed"


def test_unfinished_run_blocks_a_second_run_of_the_dataset(
    monkeypatch: pytest.MonkeyPatch, pipeline_settings: Settings
) -> None:
    _failing_floorplan(monkeypatch)
    runner = PipelineRunner.from_settings(pipeline_settings, offline=True)
    paused = runner.run_sync(DATASET)
    assert paused.status == RunStatus.NEEDS_REVIEW

    with pytest.raises(RunStateError):
        runner.create(DATASET)


def test_runs_of_a_stopped_process_are_failed(pipeline_settings: Settings) -> None:
    first = PipelineRunner.from_settings(pipeline_settings, offline=True)
    orphan = RunRecord(run_id="orphan", dataset=DATASET, status=RunStatus.RUNNING, owner_pid=None)
    first.workspace.runs.save(orphan)

    PipelineRunner.from_settings(pipeline_settings, offline=True)

    assert first.get("orphan").status == RunStatus.FAILED


def test_variant_retry_skips_types_already_created() -> None:
    state: Any = {
        "reports": {
            "equipment": {"metrics": {"to_create": ["a", "b"], "missing_types": ["a", "b"]}},
            "variant_creator": {"metrics": {"created": ["a"]}},
        }
    }
    assert pipeline_graph._variant_inputs(state)[0] == ["b"]


@pytest.mark.parametrize("wrap", ["object", "json", "wrapped_json", "wrapped_object"])
def test_spec_tool_argument_accepts_what_models_send(wrap: str) -> None:
    # Seen live: models send the spec as a JSON string or nested under "spec".
    from pydantic import TypeAdapter

    from app.pipeline.agents.variant_creator import SpecArg

    spec = {
        "type": "x",
        "category": "c",
        "default_height_m": 1,
        "primitives": [{"kind": "box", "center": [0, 0, 0.5], "size": [1, 1, 1]}],
    }
    raw: Any = {
        "object": spec,
        "json": json.dumps(spec),
        "wrapped_json": json.dumps({"spec": spec}),
        "wrapped_object": {"spec": spec},
    }[wrap]
    assert TypeAdapter(SpecArg).validate_python(raw).type == "x"


def test_summarizer_maps_short_space_ids_from_the_model(pipeline_settings: Settings) -> None:
    # Seen live: briefs carry short ids, so the model answers with short ids.
    from app.pipeline.agents import summarizer
    from app.pipeline.agents.base import AgentModels, StageContext
    from app.pipeline.tools.stats import compute_location_stats, space_brief

    runner = PipelineRunner.from_settings(pipeline_settings, offline=True)
    runner.run_sync(DATASET)
    building = runner.workspace.locations.read_building(DATASET)
    from app.domain.building import BuildingFile

    stats = compute_location_stats(BuildingFile.model_validate(building))

    def parse(**kwargs: Any) -> Any:
        if kwargs["output_format"] is summarizer.SpaceTexts:
            lines = kwargs["messages"][0]["content"].splitlines()
            items = [
                summarizer.SpaceText(space_id=line.split(" | ")[0], description="LLM text.")
                for line in lines
            ]
            parsed: Any = summarizer.SpaceTexts(items=items)
        else:
            parsed = summarizer.BuildingText(summary="LLM summary.", key_facts=[], floors=[])
        return SimpleNamespace(
            parsed_output=parsed, stop_reason="end_turn", stop_details=None, usage=None
        )

    client: Any = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse)))
    ctx = StageContext(
        client=client,
        models=AgentModels("o", "v", "w", "claude-haiku-5-5"),
        workspace=runner.workspace,
        run_id="r",
        location_id=DATASET,
        dataset_dir=pipeline_settings.datasets_dir / DATASET,
    )
    report = summarizer.run(ctx)

    location = runner.workspace.locations.get(DATASET)
    assert location is not None and location.metadata is not None
    assert {s.description for s in location.metadata.spaces.values()} == {"LLM text."}
    assert report.open_issues == []
    assert space_brief(next(iter(stats.spaces.values()))).split(" | ")[0] != next(
        iter(stats.spaces)
    )


def test_offline_summary_describes_what_the_site_is_for() -> None:
    from app.domain.building import BuildingFile
    from app.pipeline.agents.summarizer import _building_template
    from app.pipeline.tools.stats import compute_location_stats

    raw = (BACKEND_DIR / "data" / "datasets" / DATASET / "building.json").read_text()
    text = _building_template(compute_location_stats(BuildingFile.model_validate_json(raw)))

    assert text.summary.startswith("Fertigung Ost 27 is a two-storey production site for ")
    assert "CNC milling" in text.summary and "injection moulding" in text.summary
    assert "forklift" not in text.summary
    assert text.floors[1].description.startswith("Floor 1: offices")


def test_offline_suggestions_use_real_names() -> None:
    from app.domain.building import BuildingFile
    from app.pipeline.agents.suggester import MAX_LEN, template_suggestions
    from app.pipeline.tools.stats import compute_location_stats

    raw = (BACKEND_DIR / "data" / "datasets" / DATASET / "building.json").read_text()
    stats = compute_location_stats(BuildingFile.model_validate_json(raw))
    suggestions = template_suggestions(stats)

    assert len(suggestions) == 3 and len(set(suggestions)) == 3
    assert all(s.strip() and len(s) <= MAX_LEN for s in suggestions)
    names = {s.name for s in stats.spaces.values()} | {f.name for f in stats.floors}
    assert any(n in s for s in suggestions for n in names)


def test_pipeline_writes_three_suggestions(runner: PipelineRunner) -> None:
    runner.run_sync(DATASET)
    location = runner.workspace.locations.get(DATASET)
    assert location is not None and location.metadata is not None
    assert len(location.metadata.suggestions) == 3

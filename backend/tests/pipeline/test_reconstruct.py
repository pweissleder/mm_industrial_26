"""Reconstruction of building.json from floor plans (SVG parse, assembly, vision, pipeline)."""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from shapely.geometry import Polygon

from app.config import BACKEND_DIR, Settings
from app.domain.building import BuildingFile
from app.pipeline.agents import reconstructor
from app.pipeline.agents.base import AgentModels, StageContext
from app.pipeline.runner import PipelineRunner
from app.pipeline.state import RunStatus
from app.pipeline.store import Workspace
from app.pipeline.tools.reconstruct import Overrides, Reconstruction, assemble, read_equipment
from app.pipeline.tools.svgplan import PlanFloor, fit_transform, parse_floor_svg
from navgraph.builder import BuildingBuilder
from navgraph.model import Building

DATASETS = BACKEND_DIR / "data" / "datasets"
WO = DATASETS / "test_WO"
RESPLAN = DATASETS / "resplan-102"
PROC = DATASETS / "proc-factory-dev-003"


def _truth(path: Path) -> dict[str, Any]:
    return json.loads((path / "building.json").read_text())


def _wo() -> Reconstruction:
    floor = parse_floor_svg(WO / "floor_0.svg", 0)
    return assemble("resplan-102", [floor], read_equipment(WO / "equipment.csv"))


def _proc_dir(tmp_path: Path, drop_level: int | None = None) -> Path:
    dest = tmp_path / "proc"
    dest.mkdir()
    for svg in PROC.glob("floor_*.svg"):
        shutil.copy(svg, dest / svg.name)
    rows = (PROC / "equipment.csv").read_text().splitlines()
    if drop_level is not None:
        header = rows[0].split(",").index("floor_level")
        rows = [rows[0]] + [r for r in rows[1:] if r.split(",")[header] != str(drop_level)]
    (dest / "equipment.csv").write_text("\n".join(rows) + "\n")
    return dest


def _proc_floors(directory: Path) -> list[PlanFloor]:
    return [parse_floor_svg(directory / f"floor_{level}.svg", level) for level in (0, 1)]


def _proc_truth_polygons() -> dict[str, Polygon]:
    return {s["id"]: Polygon(s["polygon"]) for s in _truth(PROC)["spaces"]}


def _reprefix(value: str) -> str:
    return value.replace("proc-factory-dev-003", "proc-copy")


# 1. SVG parsing


def test_parse_resplan_svg() -> None:
    floor = parse_floor_svg(RESPLAN / "floor_0.svg", 0)

    assert len(floor.spaces) == 7
    kinds = sorted(o.kind for o in floor.openings)
    assert kinds == ["door"] * 5 + ["entrance", "opening"]
    assert len(floor.asset_centres_px) == 19
    assert len(floor.labels) == 4
    assert floor.building_name == "Residence 102"
    assert floor.floor_name == "Ground floor"


def test_fit_transform_residual_is_tiny() -> None:
    floor = parse_floor_svg(RESPLAN / "floor_0.svg", 0)
    rows = read_equipment(RESPLAN / "equipment.csv")
    fit = fit_transform(floor.asset_centres_px, [(float(r["x_m"]), float(r["y_m"])) for r in rows])

    assert fit is not None
    assert fit[1] < 1e-3


# 2. assemble vs ground truth


def test_assemble_matches_resplan_truth() -> None:
    result = _wo()
    truth = _truth(RESPLAN)
    built = result.building.model_dump()

    BuildingFile.model_validate(built)
    t_spaces = {s["id"]: s for s in truth["spaces"]}
    b_spaces = {s["id"]: s for s in built["spaces"]}
    assert set(b_spaces) == set(t_spaces)
    for sid, expected in t_spaces.items():
        got = b_spaces[sid]
        assert Polygon(got["polygon"]).hausdorff_distance(Polygon(expected["polygon"])) < 1e-3
        for key in ("type", "room_type", "name", "ref"):
            assert got[key] == expected[key], (sid, key)

    def key(o: dict[str, Any]) -> tuple[float, float]:
        return (round(o["position"][0], 2), round(o["position"][1], 2))

    t_open = sorted(truth["openings"], key=key)
    b_open = sorted(built["openings"], key=key)
    assert len(b_open) == len(t_open) == 7
    for got, expected in zip(b_open, t_open, strict=True):
        assert got["position"] == pytest.approx(expected["position"], abs=1e-3)
        assert got["kind"] == expected["kind"]
        assert set(got["space_ids"]) == set(expected["space_ids"])

    t_assets = {a["id"]: a for a in truth["assets"]}
    b_assets = {a["id"]: a for a in built["assets"]}
    assert len(b_assets) == 19
    assert set(b_assets) == set(t_assets)
    for aid, expected in t_assets.items():
        assert b_assets[aid]["space_id"] == expected["space_id"]

    assert "floor_heights" in {g["kind"] for g in result.gaps}


# 3. multi-floor


def test_multi_floor_reconstruction(tmp_path: Path) -> None:
    directory = _proc_dir(tmp_path)
    rows = read_equipment(directory / "equipment.csv")
    result = assemble("proc-copy", _proc_floors(directory), rows)
    built = result.building

    assert len(built.floors) == 2
    assert len(built.spaces) == 79
    assert len(built.openings) == 85
    assert len(built.vertical_connectors) == 2
    by_kind = {c.kind: c for c in built.vertical_connectors}
    assert set(by_kind) == {"stairs", "lift"}
    truth = {c["kind"]: c for c in _truth(PROC)["vertical_connectors"]}
    for kind, connector in by_kind.items():
        assert sorted(connector.space_ids) == sorted(_reprefix(s) for s in truth[kind]["space_ids"])

    issues = BuildingBuilder(Building.from_dict(built.model_dump())).validate()
    assert [i for i in issues if i["severity"] == "error"] == []


# 4. floor without equipment


def test_floor_without_equipment_is_aligned_by_connectors(tmp_path: Path) -> None:
    directory = _proc_dir(tmp_path, drop_level=1)
    rows = read_equipment(directory / "equipment.csv")
    result = assemble("proc-factory-dev-003", _proc_floors(directory), rows)

    assert any(g["kind"] == "floor_alignment" and g["target"] == 1 for g in result.gaps)
    truth = _proc_truth_polygons()
    spaces = [s for s in result.building.spaces if s.floor_id.endswith("F1")]
    assert spaces
    for space in spaces:
        expected = truth[space.id]
        assert Polygon(space.polygon).hausdorff_distance(expected) < 0.05, space.id


# 5. no CSV


def test_no_equipment_csv_guesses_scale(tmp_path: Path) -> None:
    directory = _proc_dir(tmp_path)
    result = assemble("proc-copy", _proc_floors(directory), [])

    assert any(g["kind"] == "scale" for g in result.gaps)
    BuildingFile.model_validate(result.building.model_dump())


# 6. overrides


def test_overrides_change_room_type_name_and_floor_height() -> None:
    floor = parse_floor_svg(WO / "floor_0.svg", 0)
    rows: list[dict[str, str]] = []  # CSV names win over derived ones, so assemble without
    plain = assemble("resplan-102", [floor], rows)
    target = plain.building.spaces[0]
    overrides = Overrides()
    overrides.spaces[target.id] = {"room_type": "office"}
    overrides.floors[0] = {"elevation_m": 0, "height_m": 4}

    result = assemble("resplan-102", [floor], rows, overrides)

    changed = next(s for s in result.building.spaces if s.id == target.id)
    assert changed.room_type == "office"
    assert changed.name != target.name
    assert "office" in changed.name.lower()
    assert result.building.floors[0].height_m == 4
    assert plain.building.floors[0].height_m == 3.0


# 7. vision fallback


def _vision_dataset(tmp_path: Path) -> Path:
    directory = tmp_path / "scan"
    directory.mkdir()
    shutil.copy(RESPLAN / "floor_0_plain.png", directory / "floor_0.png")
    shutil.copy(RESPLAN / "equipment.csv", directory / "equipment.csv")
    return directory


def _ctx(tmp_path: Path, directory: Path, client: Any) -> StageContext:
    workspace = Workspace(tmp_path / "ws", tmp_path / "seed")
    return StageContext(
        client=client,
        models=AgentModels("o", "v", "w", "s"),
        workspace=workspace,
        run_id="r",
        location_id=directory.name,
        dataset_dir=directory,
    )


def _traced() -> reconstructor.TracedFloor:
    return reconstructor.TracedFloor(
        floor_name="Ground floor",
        building_name="Scan",
        metres_per_px=0.05,
        spaces=[
            reconstructor.TracedSpace(
                type="room",
                room_type="office",
                name="Office",
                polygon_px=[[0, 0], [100, 0], [100, 100], [0, 100]],
            ),
            reconstructor.TracedSpace(
                type="room",
                room_type="meeting_room",
                name="Meeting",
                polygon_px=[[100, 0], [200, 0], [200, 100], [100, 100]],
            ),
        ],
        openings=[
            reconstructor.TracedOpening(kind="door", position_px=[100, 50]),
            reconstructor.TracedOpening(kind="entrance", position_px=[0, 50]),
        ],
    )


def test_vision_fallback_traces_image_only_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = _vision_dataset(tmp_path)
    (directory / "equipment.csv").unlink()
    usage = reconstructor.Usage()

    def fake_trace(*_a: Any, **_k: Any) -> tuple[reconstructor.TracedFloor, Any]:
        return _traced(), usage

    monkeypatch.setattr(reconstructor, "run_structured", fake_trace)
    monkeypatch.setattr(
        reconstructor,
        "fill_gaps",
        lambda ctx, floors, rows, overrides, first, files, source_ref: (
            first,
            reconstructor.GapReport(),
            usage,
        ),
    )
    ctx = _ctx(tmp_path, directory, SimpleNamespace())

    report = reconstructor.run(ctx)

    assert report.status == "passed", report
    assert (directory / "building.json").is_file()
    provenance = json.loads((directory / "reconstruction.json").read_text())
    assert provenance["methods"] == {"0": "vision"}
    built = BuildingFile.model_validate_json((directory / "building.json").read_text())
    assert len(built.spaces) == 2
    assert {o.kind for o in built.openings} == {"door", "entrance"}


def test_vision_fallback_offline_fails_asking_for_api_key(tmp_path: Path) -> None:
    directory = _vision_dataset(tmp_path)

    report = reconstructor.run(_ctx(tmp_path, directory, None))

    assert report.status == "failed"
    assert "API key" in report.summary
    assert not (directory / "building.json").exists()


# 8. pipeline integration


@pytest.fixture
def wo_runner(settings: Settings, tmp_path: Path) -> PipelineRunner:
    datasets = tmp_path / "datasets"
    shutil.copytree(WO, datasets / "test_WO")
    settings.datasets_dir = datasets
    settings.workspace_dir = tmp_path / "workspace"
    return PipelineRunner.from_settings(settings, offline=True)


@pytest.mark.parametrize("mode", ["graph", "full"])
def test_pipeline_reconstructs_then_validates(wo_runner: PipelineRunner, mode: Any) -> None:
    record = wo_runner.run_sync("test_WO", mode=mode)

    assert record.status == RunStatus.COMPLETED, record
    assert record.location_id == "test_WO"
    for stage in ("reconstruct", "raw_validation", "graph_build", "graph_validate"):
        assert record.stages[stage].status == "passed", stage
    directory = wo_runner.settings.datasets_dir / "test_WO"
    assert (directory / "building.json").is_file()
    assert (directory / "reconstruction.json").is_file()


def test_pipeline_rerun_is_unchanged_and_force_reconstructs(wo_runner: PipelineRunner) -> None:
    wo_runner.run_sync("test_WO", mode="graph")

    again = wo_runner.run_sync("test_WO", mode="graph")
    assert again.status == RunStatus.COMPLETED
    assert again.stages["reconstruct"].status == "skipped"
    assert again.stages["raw_validation"].status == "pending"

    forced = wo_runner.run_sync("test_WO", mode="graph", force=True)
    assert forced.status == RunStatus.COMPLETED
    assert forced.stages["reconstruct"].status == "passed"
    assert forced.stages["raw_validation"].status == "passed"


def test_dataset_with_building_json_skips_reconstruct(settings: Settings, tmp_path: Path) -> None:
    datasets = tmp_path / "datasets"
    shutil.copytree(RESPLAN, datasets / "resplan-102")
    settings.datasets_dir = datasets
    settings.workspace_dir = tmp_path / "workspace"
    runner = PipelineRunner.from_settings(settings, offline=True)

    record = runner.run_sync("resplan-102", mode="graph")

    assert record.status == RunStatus.COMPLETED, record
    assert record.stages["reconstruct"].status == "skipped"
    assert not (datasets / "resplan-102" / "reconstruction.json").exists()

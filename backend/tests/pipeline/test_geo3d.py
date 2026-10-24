"""Geometry engine: frames, floor plan, assets, GLB export, validation and previews."""

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.domain.building import BuildingFile
from app.pipeline.tools.geo3d.assets import place_assets
from app.pipeline.tools.geo3d.floorplan import FloorplanResult, build_floorplan
from app.pipeline.tools.geo3d.frame import (
    asset_matrix,
    decompose_asset_matrix,
    from_gltf_points,
    to_gltf_points,
)
from app.pipeline.tools.geo3d.model import export_model
from app.pipeline.tools.geo3d.preview import render_floor_plans, render_spec
from app.pipeline.tools.geo3d.spec import fallback_spec
from app.pipeline.tools.geo3d.validate import POS_TOL, ValidationReport, validate_model

BACKEND = Path(__file__).resolve().parents[2]
FACTORY = BACKEND / "data" / "datasets" / "proc-factory-dev-003"
KIT = BACKEND.parent / "problem_statement" / "industrial-agents-hackathon-2026" / "data"


def no_library(asset_type: str, category: str) -> None:
    return None


def load(path: Path) -> BuildingFile:
    return BuildingFile.model_validate_json((path / "building.json").read_text())


# --- frames ---------------------------------------------------------------------------


@pytest.mark.parametrize("rot", [0.0, 30.0, 90.0, 135.0, 270.0, -45.0])
def test_rotation_sign_width_points_along_rotation(rot: float) -> None:
    m = asset_matrix(3.0, -2.0, 1.5, 2.0, 0.5, 1.2, rot)
    # local +x end of the unit box (dataset frame) -> glTF -> placed -> dataset
    local = to_gltf_points(np.array([[0.5, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 1.0]]))
    placed = (m @ np.column_stack([local, np.ones(3)]).T).T[:, :3]
    ds = from_gltf_points(placed)
    centre, tip, top = ds[1], ds[0], ds[2]
    np.testing.assert_allclose(centre, [3.0, -2.0, 1.5], atol=1e-12)
    t = math.radians(rot)
    # width 2.0 -> half-width 1.0 along (cos t, sin t), counter-clockwise about +z
    np.testing.assert_allclose(tip - centre, [math.cos(t), math.sin(t), 0.0], atol=1e-12)
    np.testing.assert_allclose(top - centre, [0.0, 0.0, 1.2], atol=1e-12)


def test_frame_round_trip() -> None:
    rng = np.random.default_rng(0)
    for _ in range(50):
        x, y, z = rng.uniform(-100, 100, 3)
        w, d, h = rng.uniform(0.1, 10, 3)
        r = float(rng.uniform(-720, 720))
        got = decompose_asset_matrix(asset_matrix(x, y, z, w, d, h, r))
        np.testing.assert_allclose(got[:6], [x, y, z, w, d, h], atol=1e-9)
        diff = (got[6] - r) % 360.0
        assert min(diff, 360 - diff) < 1e-9


# --- full pipeline on the factory -------------------------------------------------------


@pytest.fixture(scope="module")
def factory_model(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    building = load(FACTORY)
    result = build_floorplan(building)
    placement = place_assets(result, building, no_library)
    out = tmp_path_factory.mktemp("factory")
    glb, index = export_model(result.scene, result.index, out)
    return {"building": building, "result": result, "placement": placement, "glb": glb,
            "index": index, "out": out}  # fmt: skip


def test_factory_builds_and_validates(factory_model: dict[str, Any]) -> None:
    building: BuildingFile = factory_model["building"]
    placement = factory_model["placement"]
    assert placement.placed == len(building.assets)
    assert placement.missing_types  # nothing resolved -> every type fell back
    assert sum(placement.by_type.values()) == placement.placed
    report = validate_model(factory_model["glb"], factory_model["index"], building)
    assert report.passed, report.failures[:5]
    assert report.checked["spaces"] == len(building.spaces)
    assert report.checked["openings"] == len(building.openings)
    assert report.checked["assets"] == len(building.assets)
    assert report.checked["connectors"] == len(building.vertical_connectors)
    assert max(report.max_error.values()) < POS_TOL / 10
    # the 8 m hall floor with an office level over part of it caps walls
    assert factory_model["result"].stats["capped_walls"] > 0


def test_floorplan_only_model_validates(tmp_path: Path) -> None:
    building = load(FACTORY)
    result = build_floorplan(building)
    glb, index = export_model(result.scene, result.index, tmp_path, name="floorplan")
    assert index.name == "floorplan_index.json"
    report = validate_model(glb, index, building, expect_assets=False)
    assert report.passed, report.failures[:5]
    assert "assets" not in report.checked


def _corrupt(building: BuildingFile, tmp_path: Path) -> tuple[ValidationReport, str, str]:
    result: FloorplanResult = build_floorplan(building)
    place_assets(result, building, no_library)
    asset = building.assets[0]
    matrix, geom = result.scene.graph.get(asset.id)
    moved = np.array(matrix)
    moved[0, 3] += 0.05  # 5 cm off along x
    result.scene.graph.update(frame_to=asset.id, matrix=moved)
    space = building.spaces[0]
    result.scene.graph.transforms.remove_node(space.id)
    result.scene.geometry.pop(f"mesh:{space.id}")
    glb, index = export_model(result.scene, result.index, tmp_path)
    return validate_model(glb, index, building), asset.id, space.id


def test_corrupted_model_fails_with_responsible_stage(tmp_path: Path) -> None:
    building = load(FACTORY)
    report, asset_id, space_id = _corrupt(building, tmp_path)
    assert not report.passed
    by_entity = {(f.entity_id, f.kind): f for f in report.failures}
    moved = by_entity[(asset_id, "asset_xy_m_mismatch")]
    assert moved.stage == "equipment"
    missing = by_entity[(space_id, "missing_node")]
    assert missing.stage == "floorplan"
    assert report.max_error["asset_xy_m"] == pytest.approx(0.05, abs=1e-6)


def test_previews(factory_model: dict[str, Any], tmp_path: Path) -> None:
    building: BuildingFile = factory_model["building"]
    paths = render_floor_plans(factory_model["glb"], factory_model["index"], building, tmp_path)
    assert [p.name for p in paths] == [f"plan_floor_{f.level}.png" for f in building.floors]
    assert all(p.stat().st_size > 10_000 for p in paths)
    spec_png = render_spec(fallback_spec("widget", "equipment", 1.2), tmp_path / "spec.png")
    assert spec_png.exists() and spec_png.stat().st_size > 5_000


# --- synthetic overlapping storeys ------------------------------------------------------


def _synthetic() -> BuildingFile:
    def space(sid: str, floor: str, typ: str, poly: list[list[float]]) -> dict[str, Any]:
        return {"id": sid, "floor_id": floor, "type": typ, "room_type": "office", "name": sid,
                "ref": sid, "polygon": poly, "synthetic": False}  # fmt: skip

    return BuildingFile.model_validate(
        {
            "building": {
                "id": "syn",
                "name": "Synthetic",
                "source": "test",
                "source_ref": {},
                "license": "CC0",
                "attribution": "",
                "split": "dev",
                "units": "m",
            },  # fmt: skip
            "floors": [
                {"id": "F0", "level": 0, "name": "G", "elevation_m": 0.0, "height_m": 8.0},
                {"id": "F1", "level": 1, "name": "1", "elevation_m": 3.5, "height_m": 3.5},
            ],
            "spaces": [
                space("R0", "F0", "room", [[0, 0], [10, 0], [10, 10], [0, 10]]),
                space("H0", "F0", "hall", [[10, 0], [30, 0], [30, 10], [10, 10]]),
                space("R1", "F1", "room", [[0, 0], [5, 0], [5, 10], [0, 10]]),
            ],
            "openings": [
                {
                    "id": "D1",
                    "floor_id": "F0",
                    "space_ids": ["R0", "H0"],
                    "kind": "door",
                    "position": [10.0, 5.0],
                    "width_m": 1.0,
                    "step_free": True,
                    "synthetic": False,
                },  # fmt: skip
            ],
            "vertical_connectors": [],
            "assets": [],
        }
    )


def _wall_points(result: FloorplanResult, space_id: str) -> np.ndarray:
    return from_gltf_points(np.asarray(result.scene.geometry[f"mesh:walls:{space_id}"].vertices))


def test_walls_capped_under_higher_slab_with_door_gap_and_open_hall() -> None:
    building = _synthetic()
    result = build_floorplan(building)
    pts = _wall_points(result, "R0")
    under = pts[pts[:, 0] < 4.9]
    free = pts[pts[:, 0] > 5.1]
    assert under[:, 2].max() == pytest.approx(3.5)  # stops under F1's slab
    assert free[:, 2].max() == pytest.approx(8.0)  # full hall height elsewhere
    assert result.index["walls:R0"]["capped"] is True
    # the door at (10, 5), 1 m wide, leaves a gap in R0's east wall
    east = pts[pts[:, 0] > 9.5]
    assert (np.abs(east[:, 1] - 5.0) >= 0.5 - 1e-6).all()
    # halls stay open; the upper room keeps its full height
    assert "walls:H0" not in result.index
    assert _wall_points(result, "R1")[:, 2].max() == pytest.approx(7.0)
    assert result.stats["capped_walls"] == 1


# --- kit reference building (absent in CI) ----------------------------------------------


def test_reference_osm_building(tmp_path: Path) -> None:
    found = [p for p in KIT.glob("*/osm-way27326387") if (p / "building.json").exists()]
    if not found:
        pytest.skip("kit data not available")
    building = load(found[0])
    assert sorted(f.level for f in building.floors) == [-2, 0, 1]
    result = build_floorplan(building)
    place_assets(result, building, no_library)
    glb, index = export_model(result.scene, result.index, tmp_path)
    report = validate_model(glb, index, building)
    assert report.passed, report.failures[:5]

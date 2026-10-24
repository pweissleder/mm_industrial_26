import csv
import json
from pathlib import Path

import pytest

from app.domain.building import BuildingFile
from app.pipeline.tools.stats import compute_location_stats, space_brief

BACKEND = Path(__file__).resolve().parents[2]
DEV = BACKEND / "data/datasets/proc-factory-dev-003/building.json"
KIT = BACKEND.parent / "problem_statement/industrial-agents-hackathon-2026/data/dev"


def _tiny(sf_second: bool = False, lift: bool = False) -> BuildingFile:
    def sp(i: str, f: str, t: str = "room") -> dict:
        return {
            "id": i,
            "floor_id": f,
            "type": t,
            "room_type": "office",
            "name": i,
            "ref": i,
            "polygon": [[0, 0], [4, 0], [4, 3], [0, 3]],
            "synthetic": False,
        }

    def op(i: str, spaces: list[str], sf: bool) -> dict:
        return {
            "id": i,
            "floor_id": "F0",
            "space_ids": spaces,
            "kind": "door",
            "position": [0, 0],
            "width_m": 0.9,
            "step_free": sf,
            "synthetic": False,
        }

    return BuildingFile.model_validate(
        {
            "building": {
                "id": "b",
                "name": "B",
                "source": "t",
                "source_ref": {},
                "license": "x",
                "attribution": "",
                "split": "dev",
                "units": "m",
            },
            "floors": [
                {"id": "F0", "level": 0, "name": "G", "elevation_m": 0, "height_m": 3},
                {"id": "F1", "level": 1, "name": "U", "elevation_m": 3, "height_m": 3},
            ],
            "spaces": [sp("A", "F0"), sp("B", "F0"), sp("C", "F0"), sp("U", "F1")],
            "openings": [
                op("o1", ["A", "outside"], True),
                op("o2", ["B", "outside"], False),
                op("o3", ["A", "C"], True),
                op("o4", ["B", "C"], True),
            ],
            "vertical_connectors": [
                {
                    "id": "v",
                    "kind": "lift" if lift else "stairs",
                    "name": "v",
                    "floor_ids": ["F0", "F1"],
                    "space_ids": ["C", "U"],
                    "step_free": lift,
                    "synthetic": False,
                }
            ],
            "assets": [
                {
                    "id": "a1",
                    "name": "chair",
                    "category": "furniture",
                    "type": "chair",
                    "space_id": "A",
                    "floor_id": "F0",
                    "position": [1, 1],
                    "footprint": {"width_m": 0.5, "depth_m": 0.5, "rotation_deg": 0},
                    "source": "generated",
                }
            ],
        }
    )


def test_step_free_stairs() -> None:
    s = compute_location_stats(_tiny())
    assert s.has_stairs and not s.has_lift and s.has_step_free_entrance
    assert s.spaces["A"].step_free_access and s.spaces["C"].step_free_access
    assert s.spaces["B"].step_free_access  # via C, not via its own steps
    assert not s.spaces["U"].step_free_access
    assert s.spaces["A"].entrances == 1 and s.spaces["A"].doors == 2
    assert s.spaces["A"].neighbours == ["C"]
    assert s.spaces["A"].equipment == {"chair": 1}
    assert s.step_free_coverage == 0.75


def test_step_free_lift() -> None:
    s = compute_location_stats(_tiny(lift=True))
    assert s.has_lift and s.spaces["U"].step_free_access
    assert s.step_free_coverage == 1.0


def test_no_step_free_entrance() -> None:
    b = _tiny()
    b.openings[0].step_free = False
    s = compute_location_stats(b)
    assert not s.has_step_free_entrance
    assert not s.spaces["A"].step_free_access
    assert s.step_free_coverage == 0.0


def test_dev_building_consistent() -> None:
    b = BuildingFile.model_validate(json.loads(DEV.read_text()))
    s = compute_location_stats(b)
    assert set(s.spaces) == {x.id for x in b.spaces}
    assert s.asset_count == len(b.assets) == sum(x.asset_count for x in s.spaces.values())
    assert sum(s.equipment_by_category.values()) == len(b.assets)
    assert s.total_area_m2 == pytest.approx(sum(f.area_m2 for f in s.floors), abs=0.01)
    assert s.space_count == len(b.spaces)
    assert s.floor_count == len(b.floors)
    assert all("×" in space_brief(x) or not x.equipment for x in s.spaces.values())
    s.model_dump_json()


@pytest.mark.skipif(not KIT.exists(), reason="starter kit data missing")
def test_manifest_agreement() -> None:
    rows = list(csv.DictReader((KIT.parent / "manifest.csv").open()))
    rows = [r for r in rows if r["split"] == "dev"][:8]
    assert rows
    for r in rows:
        path = KIT / r["building_id"] / "building.json"
        if not path.exists():
            continue
        s = compute_location_stats(BuildingFile.model_validate(json.loads(path.read_text())))
        truth = {k: r[k].strip().lower() in ("true", "1") for k in r}
        assert s.has_lift == truth["has_lift"], r["building_id"]
        assert s.has_stairs == truth["has_stairs"], r["building_id"]
        assert s.has_step_free_entrance == truth["has_step_free_entrance"], r["building_id"]
        assert s.room_count == int(r["rooms"]), r["building_id"]

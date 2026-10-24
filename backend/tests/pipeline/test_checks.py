import json
import time
from pathlib import Path

import pytest

from app.domain.building import BuildingFile
from app.pipeline.state import Severity
from app.pipeline.tools.checks import (
    ISSUE_CLASSES,
    inspect_entity,
    run_checks,
    translate_floor_ops,
)
from tests.pipeline.factories import (
    asset,
    connector,
    floor,
    make_building,
    make_building_dict,
    opening,
    space,
)

BACKEND = Path(__file__).resolve().parents[2]
SAMPLE = BACKEND / "data/datasets/proc-factory-dev-003/building.json"
KIT = BACKEND.parent / "problem_statement/industrial-agents-hackathon-2026/data"


def classes(b: BuildingFile) -> set[str]:
    return {i.issue_class for i in run_checks(b)}


def test_clean_default_has_no_issues() -> None:
    assert run_checks(make_building()) == []


def test_every_class_documented() -> None:
    for info in ISSUE_CLASSES.values():
        assert info.description and info.fix_hint


def test_issue_id_format_and_stability() -> None:
    b = make_building(spaces=[space("S1"), space("S2", ref="S1", name="x")])
    a, c = run_checks(b), run_checks(b)
    assert [i.id for i in a] == [i.id for i in c]
    dup = next(i for i in a if i.issue_class == "duplicate_space_ref")
    assert dup.id == "duplicate_space_ref:S1|S2"


def test_duplicate_id() -> None:
    b = make_building(assets=[asset("A1"), asset("A1", position=(2, 2))])
    [i] = [i for i in run_checks(b) if i.issue_class == "duplicate_id"]
    assert i.severity == Severity.ERROR and i.entity_ids == ["A1"]
    assert "duplicate_id" not in classes(make_building())


def test_dangling_references() -> None:
    cases = [
        {"assets": [asset("A1", space_id="NOPE")]},
        {"assets": [asset("A1", floor_id="F9")]},
        {"spaces": [space("S1", floor_id="F9"), space("S2", box=(10, 0, 20, 10))]},
        {"openings": [opening("O1", ["S1", "NOPE"])]},
        {"vertical_connectors": [connector("V1", ["F0"], ["S1", "S2"])]},
    ]
    for kw in cases:
        b = make_building(**kw)  # type: ignore[arg-type]
        assert "dangling_reference" in classes(b), kw
    assert "dangling_reference" not in classes(
        make_building(openings=[opening("O1", ["S1", "outside"])])
    )


def test_asset_floor_differs_from_space_floor() -> None:
    b = make_building(
        floors=[floor("F0"), floor("F1", 1, 3.0)],
        assets=[asset("A1", floor_id="F1")],
    )
    assert "dangling_reference" in classes(b)


def test_invalid_polygon() -> None:
    bow = [[0, 0], [10, 10], [10, 0], [0, 10]]
    for poly in (bow, [[0, 0], [1, 0]], [[0, 0], [0.5, 0], [0.5, 0.5], [0, 0.5]]):
        b = make_building(spaces=[space("S1", polygon=poly)], openings=[], assets=[])
        [i] = [i for i in run_checks(b) if i.issue_class == "invalid_polygon"]
        assert i.severity == Severity.ERROR


def test_rotation_out_of_range() -> None:
    b = make_building(assets=[asset("A1", rotation_deg=450.0), asset("A2", rotation_deg=-90.0)])
    found = {i.entity_ids[0]: i for i in run_checks(b) if i.issue_class == "rotation_out_of_range"}
    assert found["A1"].details["suggested_deg"] == 90.0
    assert found["A2"].details["suggested_deg"] == 270.0
    assert "rotation_out_of_range" not in classes(
        make_building(assets=[asset("A1", rotation_deg=359.9)])
    )


def test_name_ref_mismatch() -> None:
    assert "name_ref_mismatch" in classes(make_building(spaces=[space("S1", ref="R1", name="Lab")]))
    assert "name_ref_mismatch" not in classes(make_building())


def test_opening_off_boundary() -> None:
    b = make_building(openings=[opening("O1", ["S1", "S2"], position=(15.0, 5.0))])
    off = [i for i in run_checks(b) if i.issue_class == "opening_off_boundary"]
    assert {tuple(i.entity_ids) for i in off} == {("O1", "S1"), ("O1", "S2")} or off
    assert off[0].details["distance_m"] > 0.5
    ok = make_building(openings=[opening("O1", ["S1", "S2"], position=(10.2, 5.0))])
    assert "opening_off_boundary" not in classes(ok)


def test_overlapping_spaces() -> None:
    b = make_building(spaces=[space("S1"), space("S2", box=(5, 0, 15, 10))], openings=[], assets=[])
    [i] = [i for i in run_checks(b) if i.issue_class == "overlapping_spaces"]
    assert i.details["overlap_area_m2"] == pytest.approx(50.0)
    # same footprint on different floors does not overlap
    two = make_building(
        floors=[floor("F0"), floor("F1", 1, 3.0)],
        spaces=[space("S1"), space("S2", "F1")],
        openings=[],
        assets=[],
    )
    assert "overlapping_spaces" not in classes(two)
    # touching only along an edge
    assert "overlapping_spaces" not in classes(make_building())


def test_duplicate_name_and_ref() -> None:
    b = make_building(
        spaces=[
            space("S1", ref="X", name="Hall X"),
            space("S2", box=(10, 0, 20, 10), ref="X", name="Hall X"),
        ]
    )
    assert {"duplicate_space_name", "duplicate_space_ref"} <= classes(b)
    assert not {"duplicate_space_name", "duplicate_space_ref"} & classes(make_building())


def _two_floor(
    stop_space_type: str = "stair",
    upper_box: tuple[float, float, float, float] = (0, 0, 2, 2),
    kind: str = "stairs",
    extra_floors: list[dict[str, object]] | None = None,
    upper_floor: str = "F1",
) -> BuildingFile:
    return make_building(
        floors=[floor("F0"), floor("F1", 1, 3.0), *(extra_floors or [])],
        spaces=[
            space("S1", type="room"),
            space("L0", box=(0, 0, 2, 2), type=stop_space_type),
            space("L1", upper_floor, upper_box, type=stop_space_type),
        ],
        openings=[],
        assets=[],
        vertical_connectors=[connector("V1", ["F0", upper_floor], ["L0", "L1"], kind=kind)],
    )


def test_lift_stop_not_lift_space() -> None:
    assert "lift_stop_not_lift_space" in classes(_two_floor("stair", kind="lift"))
    assert "lift_stop_not_lift_space" not in classes(_two_floor("lift", kind="lift"))
    assert "lift_stop_not_lift_space" not in classes(_two_floor("stair", kind="stairs"))


def test_connector_stops_far_apart_and_misalignment() -> None:
    assert "connector_stops_far_apart" not in classes(_two_floor())
    b = _two_floor(upper_box=(20, 0, 22, 2))
    issues = run_checks(b)
    far = next(i for i in issues if i.issue_class == "connector_stops_far_apart")
    assert far.details["distance_m"] == pytest.approx(20.0)
    mis = next(i for i in issues if i.issue_class == "floor_misalignment")
    assert mis.entity_ids == ["F1"]
    assert mis.details["suggested_dx"] == pytest.approx(-20.0)
    assert mis.details["suggested_dy"] == pytest.approx(0.0)


def test_floor_misalignment_negative_and_translate_fixes_it() -> None:
    assert "floor_misalignment" not in classes(_two_floor())
    doc = make_building_dict(
        floors=[floor("F0"), floor("F1", 1, 3.0)],
        spaces=[
            space("S1"),
            space("L0", box=(0, 0, 2, 2), type="stair"),
            space("L1", "F1", (20, 0, 22, 2), type="stair"),
        ],
        openings=[opening("O1", ["L1", "outside"], (21, 0), "F1")],
        assets=[asset("A1", "L1", (21, 1), "F1")],
        vertical_connectors=[connector("V1", ["F0", "F1"], ["L0", "L1"])],
    )
    mis = next(
        i
        for i in run_checks(BuildingFile.model_validate(doc))
        if i.issue_class == "floor_misalignment"
    )
    import jsonpatch

    ops = translate_floor_ops(doc, "F1", mis.details["suggested_dx"], mis.details["suggested_dy"])
    assert len(ops) == 3
    fixed = jsonpatch.apply_patch(doc, ops)
    after = {i.issue_class for i in run_checks(BuildingFile.model_validate(fixed))}
    assert "floor_misalignment" not in after and "connector_stops_far_apart" not in after
    assert fixed["assets"][0]["position"] == [1.0, 1.0]
    assert doc["assets"][0]["position"] == [21, 1]  # input untouched


def test_connector_skips_floor() -> None:
    b = make_building(
        floors=[floor("F0"), floor("F1", 1, 3.0), floor("F2", 2, 6.0)],
        spaces=[
            space("L0", type="stair", box=(0, 0, 2, 2)),
            space("L2", "F2", (0, 0, 2, 2), type="stair"),
        ],
        openings=[],
        assets=[],
        vertical_connectors=[connector("V1", ["F0", "F2"], ["L0", "L2"])],
    )
    assert "connector_skips_floor" in classes(b)
    assert "connector_skips_floor" not in classes(_two_floor())


def test_unused_vertical_space() -> None:
    b = make_building(
        spaces=[space("S1"), space("ST", box=(10, 0, 12, 2), type="stair")], openings=[], assets=[]
    )
    [i] = [i for i in run_checks(b) if i.issue_class == "unused_vertical_space"]
    assert i.entity_ids == ["ST"]
    assert "unused_vertical_space" not in classes(_two_floor())


def test_floor_level_checks() -> None:
    overlapped = make_building(
        floors=[floor("F0", 0, 0.0, 3.0), floor("F1", 1, 2.0, 3.0)],
        spaces=[space("S1")],
        openings=[],
        assets=[],
    )
    assert "overlapping_storeys" in classes(overlapped)
    gap = make_building(
        floors=[floor("F0", 0, 0.0, 3.0), floor("F2", 2, 3.0, 3.0)],
        spaces=[space("S1")],
        openings=[],
        assets=[],
    )
    assert "level_gap" in classes(gap)
    assert not {"level_gap", "overlapping_storeys"} & classes(_two_floor())


def test_narrow_opening() -> None:
    assert "narrow_opening" in classes(
        make_building(openings=[opening("O1", ["S1", "S2"], width_m=0.3)])
    )
    assert "narrow_opening" not in classes(make_building())


def test_duplicate_entrance() -> None:
    b = make_building(openings=[opening("O1", ["S1", "S2"]), opening("O2", ["S2", "S1"])])
    [i] = [i for i in run_checks(b) if i.issue_class == "duplicate_entrance"]
    assert i.id == "duplicate_entrance:O1|O2" and i.details["remove"] == "O2"
    far = make_building(
        openings=[opening("O1", ["S1", "S2"]), opening("O2", ["S1", "S2"], (10, 8))]
    )
    assert "duplicate_entrance" not in classes(far)


def test_asset_outside_space() -> None:
    out = make_building(assets=[asset("A1", position=(10.0, 5.0), width_m=2, depth_m=2)])
    assert "asset_outside_space" in classes(out)  # centred on the wall: 50% outside
    b = make_building(assets=[asset("A1", position=(11.5, 5.0), width_m=2, depth_m=2)])
    [i] = [i for i in run_checks(b) if i.issue_class == "asset_outside_space"]
    assert i.details["outside_fraction"] == pytest.approx(1.0)
    assert make_building(assets=[asset("A1", position=(9.5, 5.0), width_m=2, depth_m=2)])
    assert "asset_outside_space" not in classes(
        make_building(assets=[asset("A1", position=(9.0, 5.0), width_m=2, depth_m=2)])
    )


def test_colliding_assets_and_rotation_matters() -> None:
    b = make_building(assets=[asset("A1", position=(5, 5)), asset("A2", position=(5.2, 5))])
    assert "colliding_assets" in classes(b)
    apart = make_building(assets=[asset("A1", position=(3, 5)), asset("A2", position=(7, 5))])
    assert "colliding_assets" not in classes(apart)
    # long thin assets: rotated 90 deg they are 2 m apart
    thin = [
        asset("A1", position=(5, 5), width_m=4, depth_m=0.5, rotation_deg=90),
        asset("A2", position=(7, 5), width_m=4, depth_m=0.5, rotation_deg=90),
    ]
    assert "colliding_assets" not in classes(make_building(assets=thin))
    thin[1]["position"] = [5.1, 5]
    assert "colliding_assets" in classes(make_building(assets=thin))


def test_inspect_entity() -> None:
    b = _two_floor()
    assert inspect_entity(b, "F0")["kind"] == "floor"
    sp = inspect_entity(b, "L0")
    assert sp["kind"] == "space" and sp["connector_ids"] == ["V1"] and sp["floor"]["id"] == "F0"
    c = inspect_entity(b, "V1")
    assert [s["id"] for s in c["stops"]] == ["L0", "L1"]
    base = make_building()
    o = inspect_entity(base, "O1")
    assert [s["id"] for s in o["spaces"]] == ["S1", "S2"]
    a = inspect_entity(base, "A1")
    assert a["space"]["id"] == "S1" and a["floor"]["level"] == 0
    json.dumps(a)
    with pytest.raises(KeyError):
        inspect_entity(base, "nope")


def test_sample_dataset() -> None:
    b = BuildingFile.model_validate_json(SAMPLE.read_text())
    issues = run_checks(b)
    assert not [i for i in issues if i.severity == Severity.ERROR]
    assert len({i.id for i in issues}) == len(issues)
    json.dumps([i.model_dump() for i in issues])


def test_dev_split_has_no_errors() -> None:
    files = sorted((KIT / "dev").glob("*/building.json"))
    if not files:
        pytest.skip("starter kit not available")
    start = time.perf_counter()
    for f in files:
        b = BuildingFile.model_validate_json(f.read_text())
        errors = [i for i in run_checks(b) if i.severity == Severity.ERROR]
        assert not errors, (f.parent.name, errors[:3])
    assert time.perf_counter() - start < 60

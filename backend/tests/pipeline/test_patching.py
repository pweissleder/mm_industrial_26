import copy
from typing import Any

from app.domain.building import BuildingFile
from app.pipeline.tools.checks import run_checks
from app.pipeline.tools.patching import (
    apply_patch,
    deterministic_fixes,
    id_index,
    integrity_errors,
)
from tests.pipeline.factories import asset, make_building_dict, opening, space


def test_id_index() -> None:
    idx = id_index(make_building_dict())
    assert idx["S2"] == "/spaces/1" and idx["A1"] == "/assets/0" and idx["F0"] == "/floors/0"


def test_apply_ok_and_input_not_mutated() -> None:
    doc = make_building_dict()
    snapshot = copy.deepcopy(doc)
    res = apply_patch(
        doc, [{"op": "replace", "path": "/spaces/0/name", "value": "Room S1 renamed"}]
    )
    assert res.ok and res.doc is not None and res.errors == []
    assert res.doc["spaces"][0]["name"] == "Room S1 renamed"
    assert doc == snapshot


def test_reject_bad_schema() -> None:
    doc = make_building_dict()
    res = apply_patch(doc, [{"op": "replace", "path": "/openings/0/width_m", "value": "wide"}])
    assert not res.ok and res.doc is None
    assert "/openings/0/width_m" in res.errors[0]
    res = apply_patch(doc, [{"op": "add", "path": "/assets/0/colour", "value": "red"}])
    assert not res.ok


def test_reject_bad_path_and_malformed_ops() -> None:
    doc = make_building_dict()
    assert not apply_patch(doc, [{"op": "remove", "path": "/assets/9"}]).ok
    assert not apply_patch(doc, [{"op": "replace", "path": "/nope/0", "value": 1}]).ok
    assert not apply_patch(doc, []).ok
    assert not apply_patch(doc, [{"op": "remove"}]).ok


def test_reject_protected_building_paths() -> None:
    doc = make_building_dict()
    for path in ("/building/id", "/building"):
        res = apply_patch(doc, [{"op": "replace", "path": path, "value": "x"}])
        assert not res.ok and "protected" in res.errors[0]


def test_reject_introduced_dangling_reference() -> None:
    doc = make_building_dict()
    res = apply_patch(doc, [{"op": "replace", "path": "/assets/0/space_id", "value": "NOPE"}])
    assert not res.ok and "dangling_reference" in res.errors[0]
    res = apply_patch(doc, [{"op": "remove", "path": "/spaces/1"}])  # O1 still references S2
    assert not res.ok


def test_reject_introduced_duplicate_id_and_bad_polygon() -> None:
    doc = make_building_dict()
    assert not apply_patch(doc, [{"op": "replace", "path": "/spaces/1/id", "value": "S1"}]).ok
    bow = [[0, 0], [10, 10], [10, 0], [0, 10]]
    assert not apply_patch(doc, [{"op": "replace", "path": "/spaces/0/polygon", "value": bow}]).ok


def test_preexisting_errors_do_not_block_unrelated_patch() -> None:
    doc = make_building_dict(assets=[asset("A1", space_id="NOPE")])
    assert integrity_errors(BuildingFile.model_validate(doc))
    res = apply_patch(doc, [{"op": "replace", "path": "/spaces/0/name", "value": "Room S1 b"}])
    assert res.ok
    fix = apply_patch(doc, [{"op": "replace", "path": "/assets/0/space_id", "value": "S1"}])
    assert fix.ok and fix.doc is not None
    assert not integrity_errors(BuildingFile.model_validate(fix.doc))


def test_new_issue_ids_reported() -> None:
    doc = make_building_dict()
    res = apply_patch(doc, [{"op": "replace", "path": "/openings/0/width_m", "value": 0.2}])
    assert res.ok and res.new_issue_ids == ["narrow_opening:O1"]


def _apply_all(doc: dict[str, Any], fixes: list[Any]) -> dict[str, Any]:
    for ops, reason, issue_ids, cls in fixes:
        assert reason and issue_ids and cls
        res = apply_patch(doc, ops)
        assert res.ok, res.errors
        assert res.doc is not None
        doc = res.doc
    return doc


def test_deterministic_fixes_rotation_and_duplicate_entrance() -> None:
    doc = make_building_dict(
        spaces=[space("S1"), space("S2", box=(10, 0, 20, 10))],
        openings=[
            opening("O1", ["S1", "S2"]),
            opening("O2", ["S1", "S2"]),
            opening("O3", ["S1", "S2"]),
            opening("O4", ["S1", "S2"], (10, 8)),
            opening("O5", ["S1", "S2"], (10, 8)),
        ],
        assets=[asset("A1", rotation_deg=450), asset("A2", position=(2, 2), rotation_deg=-30)],
    )
    issues = run_checks(BuildingFile.model_validate(doc))
    fixes = deterministic_fixes(doc, issues)
    assert {f[3] for f in fixes} == {"rotation_out_of_range", "duplicate_entrance"}
    fixed = _apply_all(doc, fixes)
    after = run_checks(BuildingFile.model_validate(fixed))
    assert not {i.issue_class for i in after} & {"rotation_out_of_range", "duplicate_entrance"}
    assert [o["id"] for o in fixed["openings"]] == ["O1", "O4"]
    assert [a["footprint"]["rotation_deg"] for a in fixed["assets"]] == [90.0, 330.0]


def test_deterministic_fixes_noop_when_clean() -> None:
    doc = make_building_dict()
    assert deterministic_fixes(doc, run_checks(BuildingFile.model_validate(doc))) == []

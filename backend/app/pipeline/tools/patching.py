"""Safe application of RFC 6902 patches to a raw building document.

Patches are applied to a deep copy, re-validated against the ``BuildingFile`` schema and
rejected when they introduce integrity errors (duplicate ids, dangling references, invalid
polygons) that were not already present. Messages are written for an LLM to act on.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

import jsonpatch
from pydantic import ValidationError

from app.domain.building import BuildingFile
from app.pipeline.state import Issue
from app.pipeline.tools.checks import run_checks

INTEGRITY_CLASSES = ("duplicate_id", "dangling_reference", "invalid_polygon")
_COLLECTIONS = ("floors", "spaces", "openings", "vertical_connectors", "assets")
_PROTECTED_PATHS = ("/building",)
_MAX_ERRORS = 8

FixTuple = tuple[list[dict[str, Any]], str, list[str], str]


@dataclass
class PatchResult:
    ok: bool
    doc: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    new_issue_ids: list[str] = field(default_factory=list)


def _integrity_issues(building: BuildingFile) -> list[Issue]:
    return [i for i in run_checks(building) if i.issue_class in INTEGRITY_CLASSES]


def integrity_errors(building: BuildingFile) -> list[str]:
    """Messages for duplicate ids, dangling references and invalid polygons."""
    return [f"[{i.issue_class}] {i.message}" for i in _integrity_issues(building)]


def id_index(doc: dict[str, Any]) -> dict[str, str]:
    """Map entity id -> JSON pointer (e.g. ``/assets/12``). First occurrence wins."""
    out: dict[str, str] = {}
    for coll in _COLLECTIONS:
        for i, item in enumerate(doc.get(coll, [])):
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                out.setdefault(item["id"], f"/{coll}/{i}")
    return out


def _op_errors(ops: Any) -> list[str]:
    if not isinstance(ops, list) or not ops:
        return ["ops must be a non-empty list of RFC 6902 operation objects."]
    errs: list[str] = []
    for n, op in enumerate(ops):
        if not isinstance(op, dict) or "op" not in op or "path" not in op:
            errs.append(f"op #{n}: must be an object with 'op' and 'path', got {op!r}")
            continue
        for key in ("path", "from"):
            p = op.get(key)
            if not isinstance(p, str):
                continue
            if p in _PROTECTED_PATHS or p == "/building/id" or p.startswith("/building/id/"):
                errs.append(
                    f"op #{n}: {key} {p!r} is protected; /building (metadata and building id) "
                    "must not be modified."
                )
    return errs


def _pydantic_errors(exc: ValidationError) -> list[str]:
    out = []
    for e in exc.errors()[:_MAX_ERRORS]:
        loc = "/" + "/".join(str(x) for x in e["loc"])
        out.append(f"schema violation at {loc}: {e['msg']}")
    if exc.error_count() > _MAX_ERRORS:
        out.append(f"... and {exc.error_count() - _MAX_ERRORS} more schema violations")
    return out


def apply_patch(doc: dict[str, Any], ops: list[dict[str, Any]]) -> PatchResult:
    """Apply ops to a deep copy of doc; the input is never mutated.

    Returns ok=False with doc=None if the ops are malformed, fail to apply, break the schema,
    or introduce integrity errors that were not present before.
    """
    errs = _op_errors(ops)
    if errs:
        return PatchResult(False, None, errs)
    work = copy.deepcopy(doc)
    try:
        patched = jsonpatch.JsonPatch(ops).apply(work, in_place=False)
    except (
        jsonpatch.JsonPatchException,
        jsonpatch.JsonPointerException,
        IndexError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        return PatchResult(
            False,
            None,
            [
                f"patch could not be applied: {exc}. Check that each path exists (arrays are "
                "addressed by index, e.g. /spaces/3/name; use id_index to find an entity's "
                "pointer), that 'add' targets an existing parent, and that 'remove' and "
                "'replace' targets exist. No change was made."
            ],
        )
    try:
        after = BuildingFile.model_validate(patched)
    except ValidationError as exc:
        return PatchResult(
            False,
            None,
            [*_pydantic_errors(exc), "The patched document no longer matches the schema."],
        )
    try:
        before: BuildingFile | None = BuildingFile.model_validate(doc)
    except ValidationError:
        before = None
    before_integrity = {i.id for i in _integrity_issues(before)} if before else set()
    after_issues = run_checks(after)
    introduced = [
        i
        for i in after_issues
        if i.issue_class in INTEGRITY_CLASSES and i.id not in before_integrity
    ]
    if introduced:
        return PatchResult(
            False,
            None,
            [f"patch introduces [{i.issue_class}]: {i.message}" for i in introduced[:_MAX_ERRORS]]
            + ["Patch rejected; adjust it so ids stay unique and references stay valid."],
        )
    before_ids = {i.id for i in run_checks(before)} if before else set()
    new_ids = [i.id for i in after_issues if i.id not in before_ids]
    return PatchResult(True, patched, [], new_ids)


def deterministic_fixes(doc: dict[str, Any], issues: list[Issue]) -> list[FixTuple]:
    """Unambiguous fixes as (ops, reason, issue_ids, issue_class); apply in the order returned."""
    fixes: list[FixTuple] = []
    idx = id_index(doc)

    # First, while the pointers recorded by the check are still valid (removals below).
    text_ops: list[dict[str, Any]] = []
    text_ids: list[str] = []
    for i in issues:
        if i.issue_class != "suspicious_text":
            continue
        text_ops += [
            {"op": "replace", "path": f"{i.details['pointer']}/{name}", "value": value}
            for name, value in i.details["replacements"].items()
        ]
        text_ids.append(i.id)
    if text_ops:
        fixes.append(
            (
                text_ops,
                f"Replace instruction-like text in {len(text_ids)} entities with neutral values "
                "(possible prompt injection; the original stays in raw.json).",
                text_ids,
                "suspicious_text",
            )
        )

    rot_ops: list[dict[str, Any]] = []
    rot_ids: list[str] = []
    for i in issues:
        if i.issue_class != "rotation_out_of_range" or i.entity_ids[0] not in idx:
            continue
        ptr = idx[i.entity_ids[0]]
        rot_ops.append(
            {
                "op": "replace",
                "path": f"{ptr}/footprint/rotation_deg",
                "value": i.details["suggested_deg"],
            }
        )
        rot_ids.append(i.id)
    if rot_ops:
        fixes.append(
            (
                rot_ops,
                f"Normalise rotation_deg of {len(rot_ops)} asset(s) into [0, 360).",
                rot_ids,
                "rotation_out_of_range",
            )
        )

    removals: list[tuple[int, str]] = []
    for i in issues:
        if i.issue_class != "duplicate_entrance":
            continue
        rid = i.details.get("remove")
        optr = idx.get(rid) if isinstance(rid, str) else None
        if optr is not None and optr.startswith("/openings/"):
            removals.append((int(optr.rsplit("/", 1)[1]), i.id))
    if removals:
        removals.sort(reverse=True)  # high indices first so lower ones stay valid
        fixes.append(
            (
                [{"op": "remove", "path": f"/openings/{n}"} for n, _ in removals],
                f"Remove {len(removals)} duplicate opening(s) (kept the earlier one).",
                [iid for _, iid in removals],
                "duplicate_entrance",
            )
        )
    return fixes

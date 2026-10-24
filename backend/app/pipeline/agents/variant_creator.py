"""Stage 3.3: create library specs for asset types that have none (or a flagged one).

One short agent run per type, in parallel: it studies the type's examples, drafts a
declarative spec (primitives in the unit box), checks it with `validate_spec` and a
preview image, and saves it. Specs are data, so nothing the agent writes is executed.
The library persists, so each type is created once across all locations.
"""

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from statistics import median
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator

from app.domain.building import BuildingFile
from app.pipeline.agents.base import (
    AgentError,
    StageContext,
    agent_tool,
    as_json,
    dataset_json,
    image_block,
    retry_note,
    run_subagent,
)
from app.pipeline.agents.equipment import MODEL_DIR, compose_model
from app.pipeline.state import Deviation, StageReport, Usage
from app.pipeline.tools.geo3d.preview import render_spec
from app.pipeline.tools.geo3d.spec import AssetSpec, LibraryEntry, validate_spec

MAX_PARALLEL = 4
SYSTEM_PROMPT = """\
You design 3D library models for building equipment as declarative specs. A spec is a
list of primitives (box, cylinder, prism) inside the unit box: x in [-0.5, 0.5] is the
footprint width, y in [-0.5, 0.5] the depth, z in [0, 1] the height, origin at the
bottom centre. Every placed asset scales the unit box to its real width x depth x
default_height_m, so design proportions, not metres. Make the object recognisable with
few primitives (<= 20), touching the floor and filling its footprint. Choose a
realistic default_height_m in metres. Validate, look at the preview, fix, then save.
Finish with the report after `save_spec` succeeded."""


class VariantReport(BaseModel):
    type: str
    saved: bool
    notes: str


def _unwrap_spec(raw: Any) -> Any:
    """Models sometimes send the spec as a JSON string or wrapped in {"spec": ...}."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise ValueError(
                f"spec must be a JSON object, and this string is not valid JSON ({exc}); "
                "send the spec as an object"
            ) from exc
    if isinstance(raw, dict) and set(raw) == {"spec"}:
        raw = raw["spec"]
    return raw


# Schema stays AssetSpec (so the model sees its fields); input is unwrapped first.
SpecArg = Annotated[AssetSpec, BeforeValidator(_unwrap_spec)]


def type_examples(building: BuildingFile, asset_type: str) -> dict[str, Any]:
    assets = [a for a in building.assets if a.type == asset_type]
    spaces = {s.id: s for s in building.spaces}
    widths = [float(a.footprint["width_m"]) for a in assets]
    depths = [float(a.footprint["depth_m"]) for a in assets]
    return {
        "type": asset_type,
        "category": assets[0].category if assets else None,
        "count": len(assets),
        "names": sorted({a.name for a in assets})[:8],
        "median_width_m": round(median(widths), 2) if widths else None,
        "median_depth_m": round(median(depths), 2) if depths else None,
        "room_types": Counter(spaces[a.space_id].room_type for a in assets).most_common(5),
    }


def _create_one(
    ctx: StageContext, building: BuildingFile, asset_type: str, reason: str
) -> tuple[VariantReport, Usage]:
    assert ctx.client is not None
    preview_dir = ctx.path(f"{MODEL_DIR}/variants")
    saved: dict[str, bool] = {"ok": False}

    @agent_tool
    def examples() -> str:
        """Names, category, footprint sizes and rooms of this type in the building."""
        return dataset_json(type_examples(building, asset_type))

    @agent_tool
    def check_spec(spec: SpecArg) -> str:
        """Validate a spec; returns problems or 'valid'.

        Args:
            spec: The asset spec.
        """
        return as_json(validate_spec(spec) or "valid")

    @agent_tool
    def preview_spec(spec: SpecArg) -> list[Any]:
        """Render iso + top views of a spec's unit-box mesh.

        Args:
            spec: The asset spec.
        """
        problems = validate_spec(spec)
        if problems:
            return [{"type": "text", "text": as_json(problems)}]
        path = render_spec(spec, preview_dir / f"{asset_type}.png")
        return image_block(path, "Preview of the spec (unit box)")

    @agent_tool
    def save_spec(spec: SpecArg) -> str:
        """Validate and save the spec to the asset library.

        Args:
            spec: The asset spec; `type` must equal the requested type.
        """
        if spec.type != asset_type:
            return f"spec.type must be {asset_type!r}"
        problems = validate_spec(spec)
        if problems:
            return as_json({"saved": False, "problems": problems})
        entry = ctx.workspace.library.save(
            LibraryEntry(spec=spec, provenance="agent", run_id=ctx.run_id)
        )
        saved["ok"] = True
        return as_json({"saved": True, "version": entry.version})

    report, usage = run_subagent(
        ctx.client,
        name=f"variant_creator:{asset_type}",
        model=ctx.models.worker,
        system=SYSTEM_PROMPT,
        user=(
            "Create the library model for the asset type below (`type` is the exact value "
            f"for spec.type).\n{dataset_json({'type': asset_type, 'why': reason})}"
            f"{retry_note(ctx)}"
        ),
        tools=[examples, check_spec, preview_spec, save_spec],
        output=VariantReport,
        max_iterations=16,
    )
    return report.model_copy(update={"saved": saved["ok"]}), usage


def run(ctx: StageContext, to_create: list[str], reasons: dict[str, str]) -> StageReport:
    if not to_create:
        return StageReport(stage="variant_creator", status="skipped", summary="Nothing to create.")
    if ctx.offline:
        return StageReport(
            stage="variant_creator",
            status="skipped",
            summary=f"Offline: {len(to_create)} types keep placeholder boxes.",
            metrics={"placeholder_types": to_create},
        )
    building = ctx.building()

    def create(asset_type: str) -> tuple[VariantReport, Usage]:
        try:
            return _create_one(ctx, building, asset_type, reasons.get(asset_type, "no model yet"))
        except AgentError as exc:
            return VariantReport(type=asset_type, saved=False, notes=str(exc)), Usage()

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        results = list(pool.map(create, to_create))

    usage = Usage()
    for _, u in results:
        usage = usage.add(u)
    failed = [r for r, _ in results if not r.saved]
    composed = compose_model(ctx, building)  # re-place assets with the new specs
    return StageReport(
        stage="variant_creator",
        status="failed" if failed else "passed",
        summary=f"Created {len(results) - len(failed)} of {len(results)} library models.",
        metrics={
            "created": [r.type for r, _ in results if r.saved],
            "failed": [r.type for r in failed],
            "still_missing": composed.placement.missing_types,
        },
        deviations=[
            Deviation(stage="variant_creator", kind="spec", entity_id=r.type, message=r.notes)
            for r in failed
        ],
        artifacts=[f"{MODEL_DIR}/{composed.glb.name}"],
        usage=usage,
    )

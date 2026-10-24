"""Stage 3.2: place every asset as an instance of its library spec.

Placement is deterministic. The agent reviews the type table (count, footprint, height,
spec provenance) and flags types whose library model is missing or implausible; those
go to the variant creator.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from pydantic import BaseModel, Field

from app.domain.building import BuildingFile
from app.pipeline.agents.base import StageContext, agent_tool, as_json, retry_note, run_subagent
from app.pipeline.state import StageReport, Usage
from app.pipeline.store import AssetLibrary
from app.pipeline.tools.geo3d.assets import AssetPlacement, place_assets
from app.pipeline.tools.geo3d.floorplan import build_floorplan
from app.pipeline.tools.geo3d.model import export_model
from app.pipeline.tools.geo3d.spec import AssetSpec

MODEL_DIR = "3d"
SYSTEM_PROMPT = """\
You check the equipment of a 3D building model. Every asset is placed at its exact
dataset position, rotation and footprint; its height and shape come from the asset
library entry of its type. Review the type table and flag types whose library entry is
missing (placeholder box) or whose height is implausible for the real object (e.g. a
4 m chair, a 0.3 m pallet rack). Flag only types you are confident about."""


class FlaggedType(BaseModel):
    type: str
    reason: str


class EquipmentReview(BaseModel):
    flagged: list[FlaggedType] = Field(default_factory=list)


@dataclass
class ComposedModel:
    placement: AssetPlacement
    glb: Path
    index: Path


def resolver(library: AssetLibrary) -> Callable[[str, str], AssetSpec | None]:
    """Library lookup for placement; fallback entries count as missing."""

    def resolve(asset_type: str, category: str) -> AssetSpec | None:
        entry = library.get(asset_type)
        return entry.spec if entry is not None and entry.provenance != "fallback" else None

    return resolve


def compose_model(ctx: StageContext, building: BuildingFile) -> ComposedModel:
    """Floorplan + all assets -> `3d/model.glb`; also used after new variants exist."""
    result = build_floorplan(building)
    placement = place_assets(result, building, resolver(ctx.workspace.library))
    glb, index = export_model(result.scene, result.index, ctx.path(MODEL_DIR), name="model")
    return ComposedModel(placement=placement, glb=glb, index=index)


def type_table(building: BuildingFile, library: AssetLibrary) -> list[dict[str, object]]:
    by_type: dict[str, list[tuple[float, float]]] = {}
    category: dict[str, str] = {}
    for asset in building.assets:
        by_type.setdefault(asset.type, []).append(
            (float(asset.footprint["width_m"]), float(asset.footprint["depth_m"]))
        )
        category[asset.type] = asset.category
    rows = []
    for asset_type, sizes in sorted(by_type.items()):
        entry = library.get(asset_type)
        rows.append(
            {
                "type": asset_type,
                "category": category[asset_type],
                "count": len(sizes),
                "median_width_m": round(median(s[0] for s in sizes), 2),
                "median_depth_m": round(median(s[1] for s in sizes), 2),
                "height_m": entry.spec.default_height_m if entry else None,
                "library": entry.provenance if entry else "missing",
            }
        )
    return rows


def run(ctx: StageContext) -> StageReport:
    building = ctx.building()
    composed = compose_model(ctx, building)
    missing = set(composed.placement.missing_types)

    usage = Usage()
    flagged: list[FlaggedType] = []
    if not ctx.offline and building.assets:
        assert ctx.client is not None
        table = type_table(building, ctx.workspace.library)

        @agent_tool
        def asset_types() -> str:
            """All asset types of this building with counts, footprints, heights, library status."""
            return as_json(table)

        review, usage = run_subagent(
            ctx.client,
            name="equipment",
            model=ctx.models.worker,
            system=SYSTEM_PROMPT,
            user=f"Review the equipment of {ctx.location_id}.{retry_note(ctx)}",
            tools=[asset_types],
            output=EquipmentReview,
            max_iterations=6,
            effort="low",
        )
        flagged = [f for f in review.flagged if f.type in {r["type"] for r in table}]

    to_create = sorted(missing | {f.type for f in flagged})
    return StageReport(
        stage="equipment",
        status="passed",
        summary=(
            f"Placed {composed.placement.placed} assets of {len(composed.placement.by_type)} "
            f"types; {len(missing)} types without a library model"
            + (f", {len(flagged)} flagged as implausible" if flagged else "")
            + "."
        ),
        metrics={
            "placed": composed.placement.placed,
            "types": len(composed.placement.by_type),
            "missing_types": sorted(missing),
            "flagged": [f.model_dump() for f in flagged],
            "to_create": to_create,
        },
        artifacts=[f"{MODEL_DIR}/{composed.glb.name}"],
        usage=usage,
    )

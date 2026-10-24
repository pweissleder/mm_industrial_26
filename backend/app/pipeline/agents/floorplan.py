"""Stage 3.1: 3D floorplan (slabs, space floors, walls with door gaps, connectors).

The geometry is deterministic (`tools.geo3d.floorplan`), so re-running it changes
nothing: only the numeric check can fail this stage. The agent compares each floor with
the dataset's plan image and records what looks wrong as review notes; the 3D
validator (with the finished model) decides whether they are real deviations.
"""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.pipeline.agents.base import (
    StageContext,
    agent_tool,
    as_json,
    image_block,
    retry_note,
    run_subagent,
)
from app.pipeline.state import StageReport, Usage
from app.pipeline.tools.geo3d.floorplan import build_floorplan
from app.pipeline.tools.geo3d.model import export_model
from app.pipeline.tools.geo3d.preview import render_floor_plans
from app.pipeline.tools.geo3d.validate import validate_model

MODEL_DIR = "3d"
SYSTEM_PROMPT = """\
You review the 3D floorplan generated from a building's data against the dataset's own
plan image of each floor (`view_plan` shows both). Modelling conventions, which are
correct and must not be reported:
- Spaces of type `hall` (production/warehouse zones, lobbies, "hall access") and
  `outdoor` have no walls; halls show only floor outlines, and wide strips mark the
  data's openings between them.
- Walls follow room outlines with gaps at every door/opening of the data.
- A storey taller than the gap to the next one (`overlapping_floors`) is an intended
  double-height space; its walls are capped under the upper slab.
- Stairs and lifts are marked spaces plus one vertical connector line per connector in
  the data; the data decides how many there are.
Report only concrete differences from the dataset plan (a room or door that the data
has but the model lacks, or vice versa, walls cutting through a door), naming the
space ref. Return ok=true when there are none."""


class FloorplanReview(BaseModel):
    ok: bool
    problems: list[str] = Field(default_factory=list)


def run(ctx: StageContext) -> StageReport:
    building = ctx.building()
    out_dir = ctx.path(MODEL_DIR)
    result = build_floorplan(building)
    glb, index = export_model(result.scene, result.index, out_dir, name="floorplan")
    validation = validate_model(glb, index, building, expect_assets=False)
    rel = f"{MODEL_DIR}/{glb.name}"

    usage = Usage()
    deviations = list(validation.failures)
    notes: list[str] = []
    if not ctx.offline and validation.passed:
        assert ctx.client is not None
        previews = render_floor_plans(glb, index, building, out_dir / "previews_floorplan")
        by_level = {p.stem.removeprefix("plan_floor_"): p for p in previews}

        @agent_tool
        def floor_stats() -> str:
            """Counts of spaces, walls, door gaps, openings and connectors in the model."""
            return as_json(
                {
                    **result.stats,
                    "floors": [f.level for f in building.floors],
                    "connectors_in_data": len(building.vertical_connectors),
                    "note": "overlapping_floors = intended double-height storeys",
                }
            )

        @agent_tool
        def view_plan(level: int) -> list[Any]:
            """Generated plan and the dataset's plan image of one floor.

            Args:
                level: Floor level, e.g. 0.
            """
            path: Path | None = by_level.get(str(level))
            if path is None:
                return [{"type": "text", "text": f"No floor {level}; have {sorted(by_level)}"}]
            blocks = image_block(path, f"Generated plan, level {level}")
            dataset_plan = ctx.dataset_dir / f"floor_{level}_plain.png"
            if dataset_plan.is_file():
                blocks += image_block(dataset_plan, f"Dataset plan, level {level}")
            return blocks

        review, usage = run_subagent(
            ctx.client,
            name="floorplan",
            model=ctx.models.worker,
            system=SYSTEM_PROMPT,
            user=f"Review the floorplan of {ctx.location_id}.{retry_note(ctx)}",
            tools=[floor_stats, view_plan],
            output=FloorplanReview,
            max_iterations=12,
            effort="low",
        )
        notes = [f"floorplan review: {p}" for p in review.problems]

    return StageReport(
        stage="floorplan",
        status="passed" if not deviations else "failed",
        summary=(
            f"Floorplan built: {result.stats.get('spaces', '?')} spaces on "
            f"{len(building.floors)} floors; numeric check "
            f"{'passed' if validation.passed else 'failed'}."
        ),
        metrics={"stats": result.stats, "max_error": validation.max_error},
        open_issues=notes + validation.warnings[:20],
        deviations=deviations,
        artifacts=[rel],
        usage=usage,
    )

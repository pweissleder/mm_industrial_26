"""Stage 3.4: check the finished model is valid and matches the dataset.

The numeric check re-reads the GLB and compares every space, opening, connector and
asset against the cleaned building (1 mm / 0.1 deg). The agent adds the visual check
(generated plan vs the dataset's floor plan image) and attributes each deviation to the
stage that must fix it.
"""

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
from app.pipeline.agents.equipment import MODEL_DIR
from app.pipeline.state import Deviation, StageReport, Usage
from app.pipeline.tools.geo3d.preview import render_floor_plans
from app.pipeline.tools.geo3d.validate import validate_model

SYSTEM_PROMPT = """\
You are the 3D model validator. A numeric validator already compared every node of the
generated model with the dataset. Your job: read its report, then compare the
generated plan of each floor with the dataset's own floor plan image (same orientation,
y up): room layout, door positions, equipment clusters, missing or extra elements.
Attribute every real deviation to the stage that must fix it: `floorplan` (spaces,
walls, openings, connectors), `equipment` (asset placement) or `variant_creator` (an
asset type's library model looks wrong). Pass the model only if you found no real
deviation; ignore styling differences (colours, labels, line widths)."""


class ModelVerdict(BaseModel):
    passed: bool
    summary: str
    deviations: list[Deviation] = Field(default_factory=list)


def run(ctx: StageContext) -> StageReport:
    building = ctx.building()
    out_dir = ctx.path(MODEL_DIR)
    glb, index = out_dir / "model.glb", out_dir / "model_index.json"
    report = validate_model(glb, index, building, expect_assets=True)
    previews = render_floor_plans(glb, index, building, out_dir / "previews")
    ctx.path("reports").mkdir(parents=True, exist_ok=True)
    ctx.path("reports/validation_3d.json").write_text(report.model_dump_json(indent=2))

    usage = Usage()
    deviations = list(report.failures)
    summary = (
        f"Numeric check {'passed' if report.passed else 'failed'}: "
        f"{sum(report.checked.values())} elements checked, max errors {report.max_error}."
    )
    if not ctx.offline:
        assert ctx.client is not None
        by_level = {p.stem.removeprefix("plan_floor_"): p for p in previews}

        @agent_tool
        def numeric_report() -> str:
            """The numeric validation report (counts, max errors, failures, warnings)."""
            return as_json(report.model_dump(mode="json"))

        @agent_tool
        def compare_floor(level: int) -> list[Any]:
            """Generated plan and the dataset's plan image of one floor, side by side.

            Args:
                level: Floor level, e.g. 0.
            """
            ours = by_level.get(str(level))
            theirs = ctx.dataset_dir / f"floor_{level}_plain.png"
            if ours is None:
                return [{"type": "text", "text": f"No floor {level}; have {sorted(by_level)}"}]
            blocks = image_block(ours, f"Generated model, level {level}")
            if theirs.is_file():
                blocks += image_block(theirs, f"Dataset plan, level {level}")
            return blocks

        verdict, usage = run_subagent(
            ctx.client,
            name="model_validator",
            model=ctx.models.validator,
            system=SYSTEM_PROMPT,
            user=(
                f"Validate the 3D model of {ctx.location_id} "
                f"({len(building.floors)} floors: {[f.level for f in building.floors]})."
                f"{retry_note(ctx)}"
            ),
            tools=[numeric_report, compare_floor],
            output=ModelVerdict,
            max_iterations=4 + 2 * len(building.floors),
            effort="high",
        )
        summary = f"{summary} {verdict.summary}"
        deviations += verdict.deviations
        if not verdict.passed and not verdict.deviations:
            deviations.append(Deviation(stage="floorplan", kind="review", message=verdict.summary))

    return StageReport(
        stage="model_validate",
        status="passed" if not deviations else "failed",
        summary=summary,
        metrics={"checked": report.checked, "max_error": report.max_error},
        open_issues=report.warnings[:20],
        deviations=deviations,
        artifacts=[f"{MODEL_DIR}/model.glb", "reports/validation_3d.json"]
        + [f"{MODEL_DIR}/previews/{p.name}" for p in previews],
        usage=usage,
    )

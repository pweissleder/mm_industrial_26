"""Stage 2b: validate the relationship graph with navgraph's building checks.

Reachability from an entrance, doors joining known spaces and sitting on walls, one
connector stop per floor, overlapping spaces. Errors fail the stage; warnings and info
are reported as open issues.
"""

from app.pipeline.agents.base import StageContext
from app.pipeline.agents.graph_builder import navgraph_building
from app.pipeline.state import StageReport
from app.pipeline.store import write_json
from navgraph.builder import BuildingBuilder

REPORT_FILE = "reports/graph_validation.json"


def run(ctx: StageContext) -> StageReport:
    issues = BuildingBuilder(navgraph_building(ctx)).validate()
    write_json(ctx.path(REPORT_FILE), issues)
    errors = [i for i in issues if i["severity"] == "error"]
    counts = {
        sev: sum(i["severity"] == sev for i in issues) for sev in ("error", "warning", "info")
    }
    return StageReport(
        stage="graph_validate",
        status="failed" if errors else "passed",
        summary=f"Graph checks: {counts['error']} errors, {counts['warning']} warnings, "
        f"{counts['info']} notes.",
        metrics=counts,
        open_issues=[f"{i['severity']} {i['element']}: {i['problem']}" for i in issues][:30],
        artifacts=[REPORT_FILE],
        attempt=ctx.attempt,
    )

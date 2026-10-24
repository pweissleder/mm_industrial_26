"""Stage 2b: validate the relationship graph. Skeleton, implemented later.

Planned: graph sanity checks (connectivity from the outside, no orphan spaces, every
opening joins the spaces it names, vertical links match connectors) and adherence to
the cleaned input; deviations are attributed to `graph_build` for a retry.
"""

from typing import Any

from app.pipeline.agents.base import StageContext
from app.pipeline.state import StageReport

SYSTEM_PROMPT = "TODO: relationship-graph validator prompt."


def tools(ctx: StageContext) -> list[Any]:
    """Graph validation toolkit (e.g. run_graph_checks, compare_with_input). TODO."""
    return []


def run(ctx: StageContext) -> StageReport:
    return StageReport(
        stage="graph_validate",
        status="skipped",
        summary="Relationship graph validator is not implemented yet.",
    )

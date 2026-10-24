"""Stage 2a: build the relationship graph of a location. Skeleton, implemented later.

Planned: an agent with a graph toolkit (nodes for floors, spaces, openings, connectors
and assets; edges for adjacency, containment and vertical links) that writes the graph
to the graph store and returns its reference in `PipelineState.graph_ref`.
"""

from typing import Any

from app.pipeline.agents.base import StageContext
from app.pipeline.state import StageReport

SYSTEM_PROMPT = "TODO: relationship-graph builder prompt."


def tools(ctx: StageContext) -> list[Any]:
    """Graph toolkit for the agent (e.g. add_nodes, add_edges, query_graph). TODO."""
    return []


def run(ctx: StageContext) -> StageReport:
    return StageReport(
        stage="graph_build",
        status="skipped",
        summary="Relationship graph builder is not implemented yet.",
    )

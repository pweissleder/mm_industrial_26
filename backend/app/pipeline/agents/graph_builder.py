"""Stage 2a: build the relationship graph of a location with navgraph.

The level-1 navgraph RoomGraph (one node per space plus "outside"; one edge per opening
and per stair/lift hop) is written to the workspace as node-link JSON. The chat agent
builds its own navgraph from the stored building, so this file is the inspectable copy.
"""

import networkx as nx

from app.pipeline.agents.base import StageContext
from app.pipeline.state import StageReport
from app.pipeline.store import write_json
from navgraph.graph import RoomGraph
from navgraph.model import Building

GRAPH_FILE = "graph/room_graph.json"


def navgraph_building(ctx: StageContext) -> Building:
    return Building.from_dict(ctx.building().model_dump())


def run(ctx: StageContext) -> StageReport:
    graph = RoomGraph(navgraph_building(ctx)).g
    write_json(ctx.path(GRAPH_FILE), nx.node_link_data(graph, edges="edges"))
    return StageReport(
        stage="graph_build",
        status="passed",
        summary=f"Room graph built: {graph.number_of_nodes()} nodes, "
        f"{graph.number_of_edges()} edges.",
        metrics={"nodes": graph.number_of_nodes(), "edges": graph.number_of_edges()},
        artifacts=[GRAPH_FILE],
        attempt=ctx.attempt,
    )

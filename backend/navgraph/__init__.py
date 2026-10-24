"""navgraph - a two-level navigation and intelligence layer for buildings.

    from navgraph import Building, NavGraph, OrganizerCost, DatasetCost, rules
    b = Building.load("data/dev/proc-factory-dev-003")
    nav = NavGraph(b, OrganizerCost())
    route = nav.route(start_space_id, goal_space_id, rules.STEP_FREE)

For agents, use `navgraph.agent.BuildingIntelligence`: one object, a dozen JSON tools.
"""
from . import rules
from .costs import CostModel, DatasetCost, OrganizerCost
from .graph import NavGraph, RoomGraph, RoomInterior, Route
from .model import OUTSIDE, Asset, Building, Connector, Floor, Opening, Space

__all__ = ["Building", "Floor", "Space", "Opening", "Connector", "Asset", "OUTSIDE",
           "NavGraph", "RoomGraph", "RoomInterior", "Route",
           "CostModel", "OrganizerCost", "DatasetCost", "rules"]

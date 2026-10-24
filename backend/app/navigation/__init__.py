"""Navigation package. Two parts, one building source (the BuildingRepository) and one scenario:

- Team (Alex): `BuildingNavigator` protocol, `NavgraphNavigator` (vendored `navgraph` engine) and
  `NavigatorRegistry` behind the LangGraph `/chat` agent and the `/buildings/{id}/graph` overlay.
- Phase A: `NavigationService` (official kit 1.1.0 router, precision router, search, nearest,
  scenario, benchmark operations, location) behind `/api/nav`, `/api/location` and the AriNav
  assistant. Its scenario (closures + revision) is the only one; the navgraph navigators mirror it
  (see `app.navigation.scenario_sync`). Contract: docs/PHASE_A_INTERFACES.md.
"""

from app.navigation.base import BuildingNavigator, NavigatorFactory
from app.navigation.errors import NavigationError
from app.navigation.navgraph_engine import NavgraphNavigator
from app.navigation.registry import NavigatorRegistry
from app.navigation.service import NavigationService, build_navigation_service

__all__ = [
    "BuildingNavigator",
    "NavgraphNavigator",
    "NavigationError",
    "NavigationService",
    "NavigatorFactory",
    "NavigatorRegistry",
    "build_navigation_service",
]

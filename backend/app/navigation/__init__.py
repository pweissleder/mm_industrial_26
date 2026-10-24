"""Navigation core: official kit 1.1.0 router, precision router, search, nearest, scenario,
benchmark operations and location resolution. See docs/PHASE_A_INTERFACES.md.

`NavigationService` is the one authoritative state (`app.state.navigation`); build it with
`build_navigation_service(settings, repository)`. Routes: `app.api.routes.navigation.router`
(`/api/nav/...`) and `app.api.routes.location.router` (`/api/location/resolve`).
"""

from app.navigation.errors import NavigationError
from app.navigation.service import NavigationService, build_navigation_service

__all__ = ["NavigationError", "NavigationService", "build_navigation_service"]

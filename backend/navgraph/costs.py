"""How long a route is. The graph asks a CostModel for every edge length, so swapping the
model changes the metric without touching the graph.

OrganizerCost  - the scoring rule for routes (dataset 1.1.0): walk door to door inside each space,
                 15 cm off the walls and around corners, each door landing 30 cm inside the space;
                 stairs 2 m per metre climbed; lifts 15 m + 1 m per metre; no way round the outside.
DatasetCost    - the old 1.0.0 answer rule: straight lines through each space's anchor point;
                 stairs 12 m per floor; lifts 15 m + 6 m per floor (per hop); outside is free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from shapely.geometry import Point
from shapely.ops import nearest_points

from .geometry import WalkableArea
from .model import Space

Pt = tuple[float, float]


class CostModel:
    name = "base"
    #: "anchor": a route starts/ends at the room's anchor point; "door": at the room's door (0 m inside)
    endpoint = "anchor"

    def walk(self, space: Space, a: Pt, b: Pt) -> float:
        raise NotImplementedError

    def walk_path(self, space: Space, a: Pt, b: Pt) -> list[Pt]:
        return [a, b]

    def stairs(self, rise_m: float, floors: int) -> float:
        raise NotImplementedError

    def lift_board(self) -> float:
        """Paid once each time you get into a lift."""
        raise NotImplementedError

    def lift_ride(self, rise_m: float, floors: int) -> float:
        """Paid per ride from one stop straight to another, `floors` levels apart."""
        raise NotImplementedError

    def outside(self, a: Pt, b: Pt) -> float | None:
        """Walk outside from entrance a to entrance b; None = not allowed."""
        return None

    #: if True the outside is one shared node with zero-cost links (fast, used by DatasetCost)
    outside_is_free = False


@dataclass
class OrganizerCost(CostModel):
    clearance_m: float = 0.15
    stairs_per_m: float = 2.0
    lift_wait_m: float = 15.0
    lift_per_m: float = 1.0
    door_landing_m: float = 0.30
    endpoint: str = "anchor"
    outside_mode: str = "forbidden"         # straight | free | forbidden
    lift_wait: str = "ride"                 # ride: 15 m once per ride | hop: 15 m per floor-to-floor hop
    _areas: dict = field(default_factory=dict, repr=False)
    _landings: dict = field(default_factory=dict, repr=False)
    name = "organizer"

    def variant(self, **changes) -> "OrganizerCost":
        """Same geometry cache, different settings (for testing assumptions against each other)."""
        from dataclasses import replace
        v = replace(self, **changes)
        v._areas = self._areas
        if v.door_landing_m == self.door_landing_m:
            v._landings = self._landings
        return v

    def label(self) -> str:
        return f"endpoint={self.endpoint}, lift_wait={self.lift_wait}, outside={self.outside_mode}"

    def area(self, space: Space) -> WalkableArea:
        key = (space.id, id(space.polygon))
        if key not in self._areas:
            self._areas[key] = WalkableArea(space.polygon, self.clearance_m)
        return self._areas[key]

    def land(self, space: Space, p: Pt) -> Pt:
        """Where a door at p lands inside the space: nearest point 30 cm in from the walls.
        The anchor (and anything already that far inside) stays where it is."""
        if p == space.anchor:
            return p
        key = (space.id, id(space.polygon), p)
        if key not in self._landings:
            inner = space.polygon.buffer(-self.door_landing_m)
            if inner.is_empty:
                inner = space.polygon.buffer(-0.05)
            if inner.is_empty:
                q = space.polygon.representative_point()
                self._landings[key] = (q.x, q.y)
            elif inner.contains(Point(p)):
                self._landings[key] = p
            else:
                q = nearest_points(inner, Point(p))[0]
                self._landings[key] = (q.x, q.y)
        return self._landings[key]

    def walk(self, space, a, b):
        la, lb = self.land(space, a), self.land(space, b)
        # edge lengths rounded to 3 decimals, like the graded rules (decides near-ties the same way)
        return round(math.dist(a, la) + self.area(space).distance(la, lb) + math.dist(lb, b), 3)

    def walk_path(self, space, a, b):
        la, lb = self.land(space, a), self.land(space, b)
        pts = self.area(space).path(la, lb)
        return ([a] if a != la else []) + pts + ([b] if b != lb else [])

    def stairs(self, rise_m, floors):
        return round(self.stairs_per_m * rise_m, 3)

    def lift_board(self):
        return self.lift_wait_m if self.lift_wait == "ride" else 0.0

    def lift_ride(self, rise_m, floors):
        return round(self.lift_per_m * rise_m + (self.lift_wait_m * floors if self.lift_wait == "hop" else 0.0), 3)

    def outside(self, a, b):
        if self.outside_mode == "forbidden":
            return None
        return 0.0 if self.outside_mode == "free" else math.dist(a, b)

    @property
    def outside_is_free(self):
        return self.outside_mode == "free"


class DatasetCost(CostModel):
    """Every crossing of a space goes in to its anchor point and out again, in straight lines."""
    name = "dataset"
    endpoint = "anchor"
    outside_is_free = True

    def walk(self, space, a, b):
        c = space.anchor
        return math.dist(a, c) + math.dist(c, b)

    def walk_path(self, space, a, b):
        return [a, space.anchor, b]

    def stairs(self, rise_m, floors):
        return 12.0 * floors

    def lift_board(self):
        return 0.0

    def lift_ride(self, rise_m, floors):
        return 15.0 + 6.0 * floors

    def outside(self, a, b):
        return 0.0

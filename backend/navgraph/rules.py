"""Who is moving and under which rules. A Profile never changes the graph; it filters it."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable

from .model import Building, Connector, Opening, Space

SpacePredicate = Callable[[Space], bool]


def _any_space(_: Space) -> bool:
    return True


def _never(_: Space) -> bool:
    return False


def is_forklift_space(space: Space) -> bool:
    """Forklifts drive in halls (aisles, bays, docks) and factory aisles, not pedestrian areas."""
    if space.type == "hall":
        return space.room_type != "lobby" or "aisle" in space.name.lower()
    name = space.name.lower()
    return ("aisle" in name or "aisle" in space.room_type) and "walkway" not in name


PRODUCTION_ZONES = {"production_zone", "assembly_zone", "additive_zone"}


def any_factory_zone(space: Space) -> bool:
    """Strictest reading of 'production bay': every factory hall zone."""
    return space.is_production_bay


def production_zone_only(space: Space) -> bool:
    """Narrower reading: production, assembly and additive zones; warehouse/logistics may be crossed."""
    return space.type == "hall" and space.room_type in PRODUCTION_ZONES


#: readings of "production bays are destinations, never shortcuts", strictest first
BAY_READINGS = [("no factory zone crossed", any_factory_zone),
                ("only production zones never crossed", production_zone_only),
                ("bays may be crossed", _never)]


@dataclass(frozen=True)
class Profile:
    """Routing rules.

    name             shown to the user / agent
    step_free        no stairs, no openings with steps (lifts, ramps, level doors only)
    min_width_m      every opening passed must be at least this wide (trolleys, forklifts)
    allow_stairs     stairs connectors usable
    allow_lifts      lift connectors usable
    allow_outside    may leave through one entrance and come back in through another
    can_enter        spaces the mover may be in at all
    no_pass_through  spaces that may be a start or goal but are never crossed
    closed           extra closed element ids for this query only (on top of Building.closed)
    """
    name: str = "walk"
    step_free: bool = False
    min_width_m: float = 0.0
    allow_stairs: bool = True
    allow_lifts: bool = True
    allow_outside: bool = True
    can_enter: SpacePredicate = _any_space
    no_pass_through: SpacePredicate = any_factory_zone
    vehicle: bool = False            # forklift / trolley: the bay rule ("drive into, not through") always applies
    closed: frozenset[str] = field(default_factory=frozenset)

    # -- checks used by the graph ---------------------------------------------------------
    def opening_ok(self, o: Opening) -> bool:
        if o.id in self.closed:
            return False
        if self.step_free and not o.step_free:
            return False
        return o.width_m + 1e-9 >= self.min_width_m

    def connector_ok(self, c: Connector) -> bool:
        if c.id in self.closed:
            return False
        if c.kind == "stairs":
            return self.allow_stairs and not self.step_free
        if c.kind == "lift":
            return self.allow_lifts and (c.step_free or not self.step_free)
        return False

    def space_ok(self, s: Space) -> bool:
        return s.id not in self.closed and self.can_enter(s)

    def with_closed(self, *ids: str) -> "Profile":
        return replace(self, closed=self.closed | frozenset(ids))

    def under(self, building: Building) -> "Profile":
        """This profile plus the building's lasting closures. Every search and check calls this
        once at its entry point, so `*_ok` above is the only availability test anywhere."""
        return self.with_closed(*building.closed) if building.closed else self


# ---- presets the agent can name ---------------------------------------------------------
WALK = Profile("walk")
STEP_FREE = Profile("step_free", step_free=True)                 # wheelchair, pram
FORKLIFT = Profile("forklift", min_width_m=3.0, allow_stairs=False, allow_lifts=False,
                   allow_outside=False, can_enter=is_forklift_space, vehicle=True)


def wide_load(width_m: float, name: str = "wide_load") -> Profile:
    """Trolley / pallet / bed: no stairs or steps, every door at least `width_m` wide."""
    return Profile(name, step_free=True, min_width_m=width_m, vehicle=True)


PRESETS = {"walk": WALK, "step_free": STEP_FREE, "forklift": FORKLIFT}

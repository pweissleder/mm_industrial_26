"""Score-aware routing, following the organizers' note on how route answers are checked.

The checker takes our ordered list of spaces, (1) checks every step goes through a real door,
opening, stair or lift and that the rules hold, then (2) measures it with its own distance rule.
Score = best length / our length (full marks within 1 %), invalid = 0.

So this module provides:
- validate(): the checker's step (1), so we never submit an invalid route;
- measure():  the checker's step (2), the shortest walk that follows a *given* list of spaces;
- robust_route(): experimental comparison, not the default answer path. Picks the list of spaces
  that scores best in the worst case over the cost details the note leaves open (where a route
  starts/ends inside a room, how the lift wait is charged), indoors unless there is no indoor route.
  It never loosens the rules: every candidate obeys the original profile, and the pick is
  re-validated against it. Its worst_case_score compares our own assumptions; it is not an
  official score.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from .costs import CostModel, OrganizerCost
from .graph import NavGraph, Route
from .model import OUTSIDE, Building
from .rules import Profile

Pt = tuple[float, float]


# ---------------------------------------------------------------------------------------------
# step (1): validity
# ---------------------------------------------------------------------------------------------
def links(b: Building, a: str, c: str, profile: Profile) -> list[tuple[str, str]]:
    """Usable ways to step directly from space a to space c: [(kind, element_id)]."""
    profile = profile.under(b)
    out = []
    for o in b.openings.values():
        if set(o.space_ids) == {a, c} and profile.opening_ok(o):
            out.append(("opening", o.id))
    if OUTSIDE not in (a, c):
        for k in b.connectors.values():
            ids = [s for _, s in k.stops]
            if a in ids and c in ids and abs(ids.index(a) - ids.index(c)) == 1 \
                    and profile.connector_ok(k):
                out.append((k.kind, k.id))
    return out


def validate(b: Building, spaces: list[str], profile: Profile) -> list[str]:
    """Everything the checker would reject. Empty list = valid."""
    profile = profile.under(b)
    problems = []
    if not spaces:
        return ["empty route"]
    for i, sid in enumerate(spaces):
        if sid == OUTSIDE:
            if not profile.allow_outside:
                problems.append(f"step {i}: goes outside, not allowed for {profile.name}")
            continue
        if sid not in b.spaces:
            problems.append(f"step {i}: unknown space {sid}")
            continue
        s = b.spaces[sid]
        if not profile.space_ok(s):
            problems.append(f"step {i}: {s.name} is closed or not allowed for {profile.name}")
        if 0 < i < len(spaces) - 1 and profile.no_pass_through(s):
            problems.append(f"step {i}: drives through production bay {s.name}")
    for i in range(len(spaces) - 1):
        a, c = spaces[i], spaces[i + 1]
        if a == c:
            problems.append(f"step {i}: repeats {a}")
        elif not links(b, a, c, profile):
            problems.append(f"step {i}: no usable door, opening, stair or lift between {a} and {c}")
    return problems


# ---------------------------------------------------------------------------------------------
# step (2): measuring a fixed list of spaces
# ---------------------------------------------------------------------------------------------
def measure(nav: NavGraph, spaces: list[str], profile: Profile, cost: CostModel | None = None) -> float:
    """Shortest walk that passes through exactly these spaces in this order (best choice of doors
    where two spaces share several). Returns inf if the list is invalid."""
    b, cost = nav.b, cost or nav.cost
    if validate(b, spaces, profile):
        return math.inf
    if len(spaces) == 1:
        return 0.0
    door = cost.endpoint == "door"
    # state: (point, riding_lift_id or None) -> cost; point = where we stand in spaces[i]
    first = b.spaces[spaces[0]]
    states: dict[tuple[Pt | str, str | None], float] = {(first.anchor, None): 0.0}
    for i in range(len(spaces) - 1):
        a, c = spaces[i], spaces[i + 1]
        nxt: dict = {}

        def put(key, v):
            if v < nxt.get(key, math.inf):
                nxt[key] = v
        for (pt, riding), d in states.items():
            def walk(target: Pt) -> float:
                if a == OUTSIDE:
                    return 0.0
                if i == 0 and door:                    # leaving the start room: counted from its door
                    return 0.0
                return cost.walk(b.spaces[a], pt, target)
            for kind, el in links(b, a, c, profile):
                if kind == "opening":
                    o = b.openings[el]
                    if a == OUTSIDE:                   # walking outside from the exit entrance to this one
                        w = cost.outside(pt, o.position)
                        if w is None:
                            continue
                        put((o.position, None), d + w)
                    else:
                        put((o.position, None), d + walk(o.position))
                else:
                    rise, floors = nav._rise(a, c)
                    if kind == "stairs":
                        put((b.spaces[c].anchor, None), d + walk(b.spaces[a].anchor) + cost.stairs(rise, floors))
                    else:
                        board = 0.0 if riding == el else walk(b.spaces[a].anchor) + cost.lift_board()
                        put((b.spaces[c].anchor, el), d + board + cost.lift_ride(rise, floors))
        if not nxt:
            return math.inf
        states = nxt
    goal = b.spaces[spaces[-1]]
    best = math.inf
    for (pt, _), d in states.items():
        tail = 0.0 if door else cost.walk(goal, pt, goal.anchor)
        best = min(best, d + tail)
    return best


# ---------------------------------------------------------------------------------------------
# picking the route that scores best whatever the open details turn out to be
# ---------------------------------------------------------------------------------------------
def assumptions(base: OrganizerCost) -> list[OrganizerCost]:
    """The details the note leaves open, as cost-model variants sharing one geometry cache."""
    return [base.variant(endpoint=e, lift_wait=l) for e in ("anchor", "door") for l in ("ride", "hop")]


@dataclass
class ScoredRoute:
    route: Route
    uses_outside: bool = False
    worst_score: float = 1.0                       # min over assumptions of best/ours (1.0 = optimal)
    scores: dict = field(default_factory=dict)     # assumption label -> score
    candidates: int = 1
    problems: list = field(default_factory=list)
    reading: str = "indoors"                       # indoors | via outside | as requested

    def to_dict(self, b: Building | None = None) -> dict:
        d = self.route.to_dict(b)
        d.update({"uses_outside": self.uses_outside, "worst_case_score": round(self.worst_score, 4),
                  "scores_by_assumption": {k: round(v, 4) for k, v in self.scores.items()},
                  "relies_on": self.reading})
        if self.problems:
            d["problems"] = self.problems
        return d


def readings(profile: Profile, prefer_indoor: bool = True) -> list[tuple[str, Profile]]:
    """Profiles to try in order. Each is the original profile or stricter (indoors only), so any
    route found is valid under the original rules. No route under them is a correct answer."""
    if prefer_indoor and profile.allow_outside:
        return [("indoors", replace(profile, allow_outside=False)), ("via outside", profile)]
    return [("as requested", profile)]


def robust_route(nav: NavGraph, start: str, goal: str, profile: Profile,
                 base: OrganizerCost | None = None, prefer_indoor: bool = True) -> ScoredRoute:
    base = base or (nav.cost if isinstance(nav.cost, OrganizerCost) else OrganizerCost())
    variants = assumptions(base)
    navs = [NavGraph(nav.b, v) for v in variants]
    for reading, prof in readings(profile, prefer_indoor):
        cands: dict[tuple, Route] = {}
        for n in navs:
            r = n.route(start, goal, prof)
            if r.reachable:
                cands.setdefault(tuple(r.spaces), r)
        if not cands:
            continue
        best_len = {v.label(): min(measure(nav, list(k), prof, v) for k in cands) for v in variants}
        choice, choice_key = None, None
        for k, r in cands.items():
            sc = {}
            for v in variants:
                L = measure(nav, list(k), prof, v)
                sc[v.label()] = (best_len[v.label()] / L if L > 0 else 1.0) if L < math.inf else 0.0
            key = (min(sc.values()), sum(sc.values()))
            if choice is None or key > choice_key:
                choice, choice_key, choice_sc = r, key, sc
        problems = validate(nav.b, choice.spaces, profile)    # against the ORIGINAL request
        if problems:
            break
        main = measure(nav, choice.spaces, profile, base)
        out = replace(choice, length_m=main, profile=profile.name)
        sr = ScoredRoute(out, OUTSIDE in choice.spaces, choice_key[0], choice_sc, len(cands))
        sr.reading = reading
        return sr
    sr = ScoredRoute(Route(False, start, goal, profile=profile.name,
                           reason=f"no {profile.name} route under the current rules and closures"), worst_score=0.0)
    sr.reading = "none"
    return sr

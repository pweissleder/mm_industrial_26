"""The two levels of the navigation graph.

Level 1 - RoomGraph: one node per space (+ "outside"); one edge per door/opening and per
          stair/lift hop. Cheap, readable, good for reachability, adjacency and explanations.

Level 2 - RoomInterior: inside one space, the points that matter (its doors, its stair/lift stops,
          its anchor point, the things in it) and the walking distance between them.

Routing - NavGraph glues all level-2 interiors together at the shared doors, so a route's length
          depends on which door you come in by and which you leave by. Shortest paths run here and
          are reported back in level-1 terms (the ordered list of spaces).

Nothing is precomputed per query: a Profile filters edges and a CostModel weighs them, lazily,
with per-space caching. Edits to the Building are picked up via Building.version."""

from __future__ import annotations

import heapq
import itertools
import math
from dataclasses import dataclass, field
from typing import Iterable

import networkx as nx

from .costs import CostModel, OrganizerCost
from .model import OUTSIDE, Building, Space
from .rules import WALK, Profile

Pt = tuple[float, float]


# ============================================================================================
# Level 1
# ============================================================================================
class RoomGraph:
    """Spaces as nodes; openings and connector hops as (multi-)edges with all their attributes."""

    def __init__(self, building: Building):
        self.b = building
        self._g: nx.MultiGraph | None = None
        self._version = -1

    @property
    def g(self) -> nx.MultiGraph:
        if self._g is None or self._version != self.b.version:
            self._g = self._build()
            self._version = self.b.version
        return self._g

    def _build(self) -> nx.MultiGraph:
        b, g = self.b, nx.MultiGraph()
        g.add_node(OUTSIDE, kind="outside", name="Outside")
        for s in b.spaces.values():
            g.add_node(s.id, kind="space", name=s.name, type=s.type, room_type=s.room_type,
                       floor_id=s.floor_id, level=b.floors[s.floor_id].level, area_m2=round(s.area_m2, 2))
        for o in b.openings.values():
            a, c = o.space_ids
            g.add_edge(a, c, key=o.id, kind=o.kind, element=o.id, width_m=o.width_m, step_free=o.step_free)
        for c in b.connectors.values():
            for (fa, sa), (fb, sb) in itertools.pairwise(c.stops):
                g.add_edge(sa, sb, key=f"{c.id}:{fa}->{fb}", kind=c.kind, element=c.id,
                           step_free=c.step_free, floors=abs(b.floors[fb].level - b.floors[fa].level))
        return g

    # -- queries -----------------------------------------------------------------------------
    def neighbours(self, space_id: str, profile: Profile = WALK) -> list[dict]:
        profile = profile.under(self.b)
        out = []
        for _, other, data in self.g.edges(space_id, data=True):
            if self._edge_ok(data, profile) and (other == OUTSIDE or profile.space_ok(self.b.spaces[other])):
                out.append({"space": other, "via": data["element"], "kind": data["kind"]})
        return out

    def reachable(self, start: str, profile: Profile = WALK) -> set[str]:
        """Every space the mover can get into from `start` (including through 'outside' if allowed).
        Production bays (profile.no_pass_through) can be entered but not crossed."""
        b = self.b
        profile = profile.under(b)
        if start != OUTSIDE and not profile.space_ok(b.spaces[start]):
            return set()
        seen, frontier = {start}, [start]
        while frontier:
            sid = frontier.pop()
            if sid != start and sid != OUTSIDE and profile.no_pass_through(b.spaces[sid]):
                continue
            if sid == OUTSIDE and not profile.allow_outside:
                continue
            for _, other, data in self.g.edges(sid, data=True):
                if other in seen or not self._edge_ok(data, profile):
                    continue
                if other != OUTSIDE and not profile.space_ok(b.spaces[other]):
                    continue
                seen.add(other)
                frontier.append(other)
        return seen

    def _edge_ok(self, data: dict, profile: Profile) -> bool:
        el = data["element"]
        if el in self.b.openings:
            return profile.opening_ok(self.b.openings[el])
        return profile.connector_ok(self.b.connectors[el])


# ============================================================================================
# Level 2
# ============================================================================================
@dataclass
class Waypoint:
    kind: str           # door | stop | anchor | asset
    id: str
    point: Pt
    label: str


class RoomInterior:
    """What is inside one space and how far apart it is."""

    def __init__(self, building: Building, space_id: str, cost: CostModel):
        self.b, self.space, self.cost = building, building.spaces[space_id], cost

    def waypoints(self, include_assets: bool = True) -> list[Waypoint]:
        b, s = self.b, self.space
        pts = [Waypoint("anchor", s.id, s.anchor, f"middle of {s.name}")]
        for oid in s.opening_ids:
            o = b.openings[oid]
            other = o.other_side(s.id)
            to = "outside" if other == OUTSIDE else b.spaces[other].name
            pts.append(Waypoint("door", oid, o.position, f"{o.kind} to {to} ({o.width_m:.2f} m)"))
        for cid in s.stop_ids:
            pts.append(Waypoint("stop", cid, s.anchor, b.connectors[cid].name))
        if include_assets:
            for aid in s.asset_ids:
                a = b.assets[aid]
                pts.append(Waypoint("asset", aid, a.position, a.name))
        return pts

    def distance(self, a: Pt, b: Pt) -> float:
        return self.cost.walk(self.space, a, b)

    def door_matrix(self) -> dict[tuple[str, str], float]:
        doors = [w for w in self.waypoints(False) if w.kind != "anchor"] + [self.waypoints(False)[0]]
        return {(x.id, y.id): round(self.distance(x.point, y.point), 2)
                for x, y in itertools.combinations(doors, 2)}


# ============================================================================================
# Routing on the glued graph
# ============================================================================================
@dataclass
class Step:
    """One edge of a route."""
    kind: str               # walk | door | stairs | lift_board | lift_ride | lift_exit | outside
    space_id: str | None    # space walked through / stop space
    element_id: str | None  # opening or connector
    length_m: float
    a: Pt | None = None
    b: Pt | None = None
    floor_id: str | None = None


@dataclass
class Route:
    reachable: bool
    start: str
    goal: str
    length_m: float = math.inf
    spaces: list[str] = field(default_factory=list)      # what the scorer checks
    elements: list[str] = field(default_factory=list)    # opening / connector per transition
    steps: list[Step] = field(default_factory=list)
    legs: list["Route"] = field(default_factory=list)    # tours
    profile: str = "walk"
    reason: str = ""

    def to_dict(self, building: Building | None = None) -> dict:
        d = {"reachable": self.reachable, "length_m": round(self.length_m, 2) if self.reachable else None,
             "spaces": self.spaces, "elements": self.elements, "profile": self.profile}
        if building:
            d["space_names"] = [building.spaces[s].name if s in building.spaces else s for s in self.spaces]
        if self.legs:
            d["legs"] = [leg.to_dict(building) for leg in self.legs]
        if self.reason:
            d["reason"] = self.reason
        return d


class NavGraph:
    """Door-level routing graph assembled from all RoomInteriors. Build once per Building."""

    def __init__(self, building: Building, cost: CostModel | None = None):
        self.b = building
        self.cost = cost or OrganizerCost()
        self.rooms = RoomGraph(building)

    def interior(self, space_id: str) -> RoomInterior:
        return RoomInterior(self.b, space_id, self.cost)

    # -- node geometry --------------------------------------------------------------------------
    def _point(self, node) -> Pt:
        kind = node[0]
        if kind == "door":
            return self.b.openings[node[1]].position
        if kind == "anchor":
            return self.b.spaces[node[1]].anchor
        return self.b.spaces[node[2]].anchor           # land / car: at the stop space's anchor

    # -- edge generation ------------------------------------------------------------------------
    def _walk(self, space: Space, a: Pt, b: Pt) -> float:
        return self.cost.walk(space, a, b)

    def _space_targets(self, sid: str, src_node, profile: Profile, start: str, goal: str):
        """Walk edges from src_node to every other point of space sid."""
        b = self.b
        space = b.spaces[sid]
        if not profile.space_ok(space):
            return
        src_pt = self._point(src_node)
        endpoint_door = self.cost.endpoint == "door"
        from_anchor = src_node[0] == "anchor"
        crossing_ok = not profile.no_pass_through(space)
        if sid == goal and not from_anchor:
            w = 0.0 if endpoint_door else self._walk(space, src_pt, space.anchor)
            yield ("anchor", sid), w, Step("walk", sid, None, w, src_pt, space.anchor, space.floor_id)
        if not (from_anchor or crossing_ok):
            return
        for oid in space.opening_ids:
            node = ("door", oid)
            if node == src_node:
                continue
            o = b.openings[oid]
            if not profile.opening_ok(o):
                continue
            w = 0.0 if (from_anchor and endpoint_door) else self._walk(space, src_pt, o.position)
            yield node, w, Step("walk", sid, None, w, src_pt, o.position, space.floor_id)
        for cid in space.stop_ids:
            node = ("land", cid, sid)
            if node == src_node:
                continue
            c = b.connectors[cid]
            if not profile.connector_ok(c):
                continue
            w = 0.0 if (from_anchor and endpoint_door) else self._walk(space, src_pt, space.anchor)
            yield node, w, Step("walk", sid, None, w, src_pt, space.anchor, space.floor_id)

    def _edges(self, node, profile: Profile, start: str, goal: str):
        b, cost = self.b, self.cost
        kind = node[0]
        if kind == "anchor":
            yield from self._space_targets(node[1], node, profile, start, goal)
        elif kind == "door":
            o = b.openings[node[1]]
            for sid in o.space_ids:
                if sid == OUTSIDE:
                    if not profile.allow_outside:
                        continue
                    if cost.outside_is_free:
                        yield ("outside",), 0.0, Step("outside", OUTSIDE, o.id, 0.0, o.position, None)
                    else:
                        for e in b.entrances():
                            if e.id == o.id or not profile.opening_ok(e):
                                continue
                            w = cost.outside(o.position, e.position)
                            if w is not None:
                                yield ("door", e.id), w, Step("outside", OUTSIDE, None, w, o.position, e.position)
                else:
                    yield from self._space_targets(sid, node, profile, start, goal)
        elif kind == "outside":
            for e in b.entrances():
                if profile.opening_ok(e):
                    yield ("door", e.id), 0.0, Step("outside", OUTSIDE, e.id, 0.0, None, e.position)
        elif kind == "land":
            _, cid, sid = node
            c = b.connectors[cid]
            yield from self._space_targets(sid, node, profile, start, goal)
            idx = [s for _, s in c.stops].index(sid)
            if c.kind == "lift":
                w = cost.lift_board()
                yield ("car", cid, sid), w, Step("lift_board", sid, cid, w, floor_id=b.spaces[sid].floor_id)
            else:
                for j in (idx - 1, idx + 1):
                    if 0 <= j < len(c.stops):
                        nxt = c.stops[j][1]
                        if not profile.space_ok(b.spaces[nxt]):
                            continue
                        rise, floors = self._rise(sid, nxt)
                        w = cost.stairs(rise, floors)
                        yield ("land", cid, nxt), w, Step("stairs", nxt, cid, w, floor_id=b.spaces[nxt].floor_id)
        elif kind == "car":
            _, cid, sid = node
            c = b.connectors[cid]
            yield ("land", cid, sid), 0.0, Step("lift_exit", sid, cid, 0.0, floor_id=b.spaces[sid].floor_id)
            idx = [s for _, s in c.stops].index(sid)
            for j in (idx - 1, idx + 1):
                if 0 <= j < len(c.stops):
                    nxt = c.stops[j][1]
                    if not profile.space_ok(b.spaces[nxt]):
                        continue
                    rise, floors = self._rise(sid, nxt)
                    w = cost.lift_ride(rise, floors)
                    yield ("car", cid, nxt), w, Step("lift_ride", nxt, cid, w, floor_id=b.spaces[nxt].floor_id)

    def _rise(self, s1: str, s2: str) -> tuple[float, int]:
        f1, f2 = self.b.floor_of(s1), self.b.floor_of(s2)
        return abs(f2.elevation_m - f1.elevation_m), abs(f2.level - f1.level)

    # -- search ---------------------------------------------------------------------------------
    def _dijkstra(self, start: str | None, goals: set[str] | None, profile: Profile, stop_at_first=True,
                  sources: list | None = None):
        """From the anchor of `start` (or from explicit `sources` nodes); returns (dist, prev, hit)."""
        srcs = sources if sources is not None else [("anchor", start)]
        dist = {src: 0.0 for src in srcs}
        prev: dict = {}
        pq = [(0.0, i, src) for i, src in enumerate(srcs)]
        heapq.heapify(pq)
        tie = itertools.count(len(srcs))
        while pq:
            d, _, u = heapq.heappop(pq)
            if d > dist.get(u, math.inf):
                continue
            if u[0] == "anchor" and u[1] != start and goals is not None and u[1] in goals and stop_at_first:
                return dist, prev, u
            if u[0] == "anchor" and u[1] != start:
                continue                       # anchors are dead ends (only reached as goals)
            goal_for_edges = None
            for v, w, step in self._edges_multi(u, profile, start, goals):
                nd = d + w
                if nd < dist.get(v, math.inf) - 1e-12:
                    dist[v] = nd
                    prev[v] = (u, step)
                    heapq.heappush(pq, (nd, next(tie), v))
        return dist, prev, None

    def _edges_multi(self, node, profile, start, goals):
        # Generate edges once; anchors of *any* goal space are offered.
        if goals is None:
            goals = set()
        seen_goal_edges = set()
        for v, w, step in self._edges(node, profile, start, goal=None):
            yield v, w, step
        # add goal-anchor edges (the generic generator only adds the anchor of `goal`)
        sids = self._spaces_of(node)
        for sid in sids:
            if sid in goals and node != ("anchor", sid):
                space = self.b.spaces[sid]
                if not profile.space_ok(space):
                    continue
                p = self._point(node)
                w = 0.0 if self.cost.endpoint == "door" else self.cost.walk(space, p, space.anchor)
                yield ("anchor", sid), w, Step("walk", sid, None, w, p, space.anchor, space.floor_id)

    def _spaces_of(self, node) -> list[str]:
        if node[0] == "door":
            return [s for s in self.b.openings[node[1]].space_ids if s != OUTSIDE]
        if node[0] in ("land",):
            return [node[2]]
        return []

    def route(self, start: str, goal: str, profile: Profile = WALK) -> Route:
        b = self.b
        profile = profile.under(b)
        for sid in (start, goal):
            if sid not in b.spaces:
                raise KeyError(sid)
            if not profile.space_ok(b.spaces[sid]):
                return Route(False, start, goal, profile=profile.name, reason=f"{b.spaces[sid].name} is closed or not accessible for {profile.name}")
        if start == goal:                      # only after both ends are known and open
            return Route(True, start, goal, 0.0, [start], [], profile=profile.name)
        dist, prev, hit = self._dijkstra(start, {goal}, profile)
        if hit is None:
            return Route(False, start, goal, profile=profile.name,
                         reason=f"no {profile.name} route under the current rules and closures")
        return self._build_route(start, goal, hit, dist, prev, profile)

    def tour(self, stops: list[str], profile: Profile = WALK) -> Route:
        legs = [self.route(a, c, profile) for a, c in itertools.pairwise(stops)]
        ok = all(leg.reachable for leg in legs)
        r = Route(ok, stops[0], stops[-1], sum(l.length_m for l in legs) if ok else math.inf, profile=profile.name,
                  legs=legs)
        if ok:
            for leg in legs:
                r.spaces += leg.spaces if not r.spaces else leg.spaces[1:]
                r.elements += leg.elements
                r.steps += leg.steps
        else:
            r.reason = next(l.reason for l in legs if not l.reachable)
        return r

    def distances_from(self, start: str, profile: Profile = WALK, targets: Iterable[str] | None = None) -> dict[str, float]:
        """Shortest route length from `start` to every (or every listed) space. One search."""
        b = self.b
        profile = profile.under(b)
        if not profile.space_ok(b.spaces[start]):
            return {}
        goals = set(targets) if targets is not None else set(b.spaces)
        goals.discard(start)
        dist, _, _ = self._dijkstra(start, goals, profile, stop_at_first=False)
        out = {start: 0.0}
        for sid in goals:
            d = dist.get(("anchor", sid))
            if d is not None:
                out[sid] = d
        return out

    def distances_to_exit(self, profile: Profile = WALK, targets: Iterable[str] | None = None) -> dict[str, float]:
        """Shortest walk from every space out of the building (to its nearest usable entrance).
        Runs one search outward from all entrances (edges are symmetric)."""
        b = self.b
        profile = profile.under(b)
        entrances = [("door", e.id) for e in b.entrances() if profile.opening_ok(e)]
        goals = set(targets) if targets is not None else set(b.spaces)
        dist, _, _ = self._dijkstra(None, goals, profile, stop_at_first=False, sources=entrances)
        return {sid: dist[("anchor", sid)] for sid in goals if ("anchor", sid) in dist}

    def nearest(self, start: str, candidates: Iterable[str], profile: Profile = WALK) -> tuple[str | None, float]:
        profile = profile.under(self.b)
        cands = set(candidates)
        if not profile.space_ok(self.b.spaces[start]):
            return None, math.inf
        if start in cands:
            return start, 0.0
        dist, prev, hit = self._dijkstra(start, cands, profile)
        return (hit[1], dist[hit]) if hit else (None, math.inf)

    def _build_route(self, start, goal, hit, dist, prev, profile) -> Route:
        hops = []                                  # (from_node, step, to_node)
        node = hit
        while node in prev:
            u, step = prev[node]
            hops.append((u, step, node))
            node = u
        hops.reverse()
        spaces, elements, pending = [start], [], None
        for u, st, v in hops:
            if st.kind == "walk":
                if st.space_id != spaces[-1]:
                    if pending:
                        elements.append(pending)
                    spaces.append(st.space_id)
                pending = None
            elif st.kind == "outside":
                if spaces[-1] != OUTSIDE:
                    if u[0] == "door":
                        elements.append(u[1])
                    spaces.append(OUTSIDE)
                pending = None
            elif st.kind in ("stairs", "lift_ride"):
                elements.append(st.element_id)
                spaces.append(st.space_id)
                pending = None
            if v[0] == "door":
                pending = v[1]
        return Route(True, start, goal, dist[hit], spaces, elements, [h[1] for h in hops], profile=profile.name)

    # -- geometry of a route (for drawing / directions) ----------------------------------------------
    def polyline(self, route: Route) -> list[dict]:
        """Per-floor polylines following the actual walking paths."""
        out: list[dict] = []
        for st in route.steps:
            if st.kind != "walk" or st.a is None or st.b is None:
                continue
            pts = self.cost.walk_path(self.b.spaces[st.space_id], st.a, st.b)
            if out and out[-1]["floor_id"] == st.floor_id:
                out[-1]["points"] += pts[1:]
            else:
                out.append({"floor_id": st.floor_id, "points": list(pts)})
        return out

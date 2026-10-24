"""The agent-facing layer: one object, a small set of JSON tools, plain-language results.

    bi = BuildingIntelligence.load("data/dev/proc-factory-dev-003")
    system_prompt = bi.primer()              # what the agent needs to know about this building
    tools = bi.tool_schemas()                # Anthropic tool definitions
    result = bi.call("route", {"start": "Office 0.30", "goal": "CNC mill", "profile": "step_free"})

Design rules for every tool:
- accepts what a person would type (names, room numbers, asset names, types) as well as ids;
- never guesses: ambiguous references come back as {"error": "ambiguous", "candidates": [...]};
- returns compact JSON with ids AND names, so the agent can both act and explain;
- never raises: problems come back as {"error": ...} the agent can relay or act on.
"""

from __future__ import annotations

import inspect
import json
import math
import re
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from shapely.geometry import Point as ShapelyPoint

from . import analysis as A
from .builder import BuildingBuilder
from .costs import CostModel, DatasetCost, OrganizerCost
from .graph import NavGraph, Route
from . import scoring
from .model import OUTSIDE, Building, Space
from .rules import FORKLIFT, PRESETS, STEP_FREE, WALK, Profile, wide_load


class ToolError(Exception):
    def __init__(self, error: str, **detail):
        super().__init__(error)
        self.payload = {"error": error, **detail}


_TOOLS: dict[str, dict] = {}


def tool(description: str, **params: dict):
    """Register a method as an agent tool. `params` are JSON-schema properties; a property with
    "required": True is required."""
    def wrap(fn: Callable):
        props, required = {}, []
        for name, spec in params.items():
            spec = dict(spec)
            if spec.pop("required", False):
                required.append(name)
            props[name] = spec
        _TOOLS[fn.__name__] = {"name": fn.__name__, "description": description,
                               "input_schema": {"type": "object", "properties": props, "required": required}}
        return fn
    return wrap


S = {"type": "string"}
N = {"type": "number"}
B = {"type": "boolean"}
PROFILE = {"type": "string", "enum": ["walk", "step_free", "forklift", "wide_load"],
           "description": "walk (default); step_free = no stairs/steps (wheelchair, trolley); "
                          "forklift = aisles/halls and openings >= 3 m only; wide_load = step-free + min_width"}


class BuildingIntelligence:
    def __init__(self, building: Building, cost: CostModel | None = None):
        self.b = building
        self.nav = NavGraph(building, cost or OrganizerCost())
        self._dataset_nav: NavGraph | None = None
        self.builder = BuildingBuilder(building)

    @classmethod
    def load(cls, path: str | Path, metric: str = "organizer") -> "BuildingIntelligence":
        b = Building.load(path)
        return cls(b, DatasetCost() if metric == "dataset" else OrganizerCost())

    @classmethod
    def new(cls, building_id: str, name: str) -> "BuildingIntelligence":
        return cls(BuildingBuilder(building_id=building_id, name=name).b)

    @property
    def dataset_nav(self) -> NavGraph:
        """Graph with the metric behind the provided answer files (for numeric benchmark answers)."""
        if self._dataset_nav is None:
            self._dataset_nav = NavGraph(self.b, DatasetCost())
        return self._dataset_nav

    # ==========================================================================================
    # Plumbing for agents
    # ==========================================================================================
    @staticmethod
    def tool_schemas() -> list[dict]:
        return [dict(t) for t in _TOOLS.values()]

    def call(self, name: str, args: dict | None = None) -> dict:
        if name not in _TOOLS:
            return {"error": "unknown_tool", "tools": sorted(_TOOLS)}
        try:
            return getattr(self, name)(**(args or {}))
        except ToolError as e:
            return e.payload
        except (KeyError, ValueError, TypeError) as e:
            return {"error": type(e).__name__, "message": str(e)}

    def call_json(self, name: str, args: dict | None = None) -> str:
        return json.dumps(self.call(name, args), ensure_ascii=False, default=_jsonable)

    def primer(self) -> str:
        """Markdown briefing about this building for the agent's system prompt."""
        b = self.b
        lines = [f"# Building: {b.name} ({b.id})", "",
                 "Units are metres; x right, y up; all floors share one XY frame.", "", "## Floors"]
        for f in b.floors_by_level():
            spaces = b.spaces_on(f.id)
            kinds = Counter(s.room_type for s in spaces)
            top = ", ".join(f"{n}× {k}" for k, n in kinds.most_common(6))
            lines.append(f"- level {f.level} = **{f.name}** (elev {f.elevation_m} m): {len(spaces)} spaces: {top}")
        lines += ["", "## Vertical connections"]
        for c in b.connectors.values():
            fl = ", ".join(b.floors[f].name for f, _ in c.stops)
            lines.append(f"- {c.name} [{c.id}] {c.kind}{' (step-free)' if c.step_free else ''}: {fl}")
        if not b.connectors:
            lines.append("- none (single floor or no stairs/lifts mapped)")
        if len(b.connectors) > 15:
            extra = len(b.connectors) - 15
            idx = lines.index("## Vertical connections")
            lines[idx + 16:idx + 1 + len(b.connectors)] = [f"- ... and {extra} more (use building_overview)"]
        lines += ["", "## Entrances"]
        for o in b.entrances()[:12]:
            inner = o.other_side(OUTSIDE)
            lines.append(f"- {o.id} into {b.spaces[inner].name} ({b.floor_of(inner).name}), "
                         f"{o.width_m} m wide{'' if o.step_free else ', has steps'}")
        if len(b.entrances()) > 12:
            lines.append(f"- ... and {len(b.entrances()) - 12} more (use building_overview)")
        cats = Counter(a.category for a in b.assets.values())
        lines += ["", f"## Things: {len(b.assets)} assets ({', '.join(f'{n} {k}' for k, n in cats.items())})",
                  "", "## How to work with this building",
                  "- Refer to places as people do ('Office 0.30', '0.30', 'the CNC mill'); tools resolve them "
                  "and return `ambiguous` with candidates instead of guessing - then ask the user.",
                  "- A route ends in the room that holds a thing, never at the thing itself.",
                  "- Routes are lists of spaces; `length_m` is walking metres (stairs 2 m per m climbed, "
                  "lift 15 m + 1 m per m).",
                  "- Rules: profile=step_free (no stairs or steps), forklift (aisles and >=3 m openings, "
                  "never through production bays), wide_load with min_width. Use `avoid` for one-off closures, "
                  "`set_closed` for lasting ones.",
                  "- If a route is impossible under the rules, say so plainly and say which rule blocks it.",
                  f"- Tools: {', '.join(_TOOLS)}."]
        if b.closed:
            lines.insert(3, f"**Currently closed:** {', '.join(sorted(b.closed))}\n")
        return "\n".join(lines)

    # ==========================================================================================
    # Resolving what people say
    # ==========================================================================================
    def resolve_space(self, ref: str, floor: str | int | None = None) -> str:
        """Space id from an id, name, room number, 'Name (Floor 1)', asset name/id or asset type."""
        b = self.b
        if ref in b.spaces:
            return ref
        if ref in b.assets:
            return b.assets[ref].space_id
        text, floor_hint = _split_floor(ref)
        floor_id = self._floor_id(floor if floor is not None else floor_hint)
        low = text.strip().lower()
        pool = [s for s in b.spaces.values() if floor_id is None or s.floor_id == floor_id]
        exact = [s for s in pool if low in (s.name.lower(), (s.ref or "").lower())]
        if len(exact) == 1:
            return exact[0].id
        if len(exact) > 1:
            raise ToolError("ambiguous", reference=ref, candidates=[self._space_brief(s) for s in exact[:12]])
        assets = [a for a in b.assets.values() if (floor_id is None or a.floor_id == floor_id)
                  and (a.name.lower() == low or a.type.replace("_", " ") == low.replace("_", " "))]
        rooms = sorted({a.space_id for a in assets})
        if len(rooms) == 1:
            return rooms[0]
        if len(rooms) > 1:
            raise ToolError("ambiguous", reference=ref, hint="several rooms hold this; pick one or use `nearest`",
                            candidates=[self._space_brief(b.spaces[r]) for r in rooms[:12]])
        hits = self.find(text, floor=floor_id)["results"]
        spaces = [h for h in hits if h["kind"] == "space"]
        if len(spaces) == 1:
            return spaces[0]["id"]
        raise ToolError("not_found" if not hits else "ambiguous", reference=ref, candidates=hits[:12])

    def _floor_id(self, floor: str | int | None) -> str | None:
        if floor is None or floor == "":
            return None
        b = self.b
        if isinstance(floor, str) and floor in b.floors:
            return floor
        if isinstance(floor, int) or re.fullmatch(r"-?\d+", str(floor)):
            f = b.floor_by_level(int(floor))
            if f:
                return f.id
        low = str(floor).lower().strip()
        for f in b.floors.values():
            if f.name.lower() == low:
                return f.id
        m = re.search(r"(-?\d+)", low)
        if m:
            lvl = int(m.group(1))
            lvl = -lvl if ("basement" in low or low.startswith("ug")) and lvl > 0 else lvl
            f = b.floor_by_level(lvl)
            if f:
                return f.id
        if any(w in low for w in ("ground", "eg", "erdgeschoss")):
            f = b.floor_by_level(0)
            return f.id if f else None
        raise ToolError("unknown_floor", floor=floor, floors=[f"{f.level}: {f.name}" for f in b.floors_by_level()])

    def _element(self, ref: str) -> str:
        b = self.b
        if ref in b.connectors or ref in b.openings or ref in b.spaces:
            return ref
        low = ref.lower().strip()
        named = [c.id for c in b.connectors.values() if c.name.lower() == low]
        if len(named) == 1:
            return named[0]
        partial = [c.id for c in b.connectors.values() if low in c.name.lower() or c.name.lower() in low]
        if len(partial) == 1:
            return partial[0]
        return self.resolve_space(ref)

    def _profile(self, profile: str = "walk", min_width: float | None = None, avoid: list[str] | None = None) -> Profile:
        if profile == "wide_load":
            p = wide_load(min_width or 1.0)
        else:
            p = PRESETS.get(profile)
            if p is None:
                raise ToolError("unknown_profile", options=sorted(PRESETS) + ["wide_load"])
            if min_width:
                p = replace(p, min_width_m=max(p.min_width_m, min_width))
        if avoid:
            p = p.with_closed(*[self._element(x) for x in avoid])
        return p

    # ==========================================================================================
    # Tools: understanding
    # ==========================================================================================
    @tool("Floors, stairs/lifts, entrances, current closures and asset counts of the building.")
    def building_overview(self) -> dict:
        b = self.b
        return {"name": b.name, "id": b.id,
                "floors": [{"id": f.id, "level": f.level, "name": f.name, "elevation_m": f.elevation_m,
                            "spaces": len(b.spaces_on(f.id))} for f in b.floors_by_level()],
                "connectors": [{"id": c.id, "name": c.name, "kind": c.kind, "step_free": c.step_free,
                                "floors": [b.floors[f].name for f, _ in c.stops]} for c in b.connectors.values()],
                "entrances": [{"id": o.id, "into": self._space_brief(b.spaces[o.other_side(OUTSIDE)]),
                               "step_free": o.step_free, "width_m": o.width_m} for o in b.entrances()],
                "closed": sorted(b.closed), "assets": len(b.assets)}

    @tool("Search rooms and things by name, room number or type (all words must match). "
          "Use it to locate things ('3D printer') and to disambiguate.",
          query={**S, "required": True}, floor={**S, "description": "optional floor name or level"},
          kind={"type": "string", "enum": ["any", "space", "asset"]})
    def find(self, query: str, floor: str | int | None = None, kind: str = "any") -> dict:
        b = self.b
        floor_id = self._floor_id(floor)
        words = [w for w in re.split(r"[\s_]+", query.lower()) if w]
        out = []
        if kind in ("any", "space"):
            for s in b.spaces.values():
                hay = f"{s.name} {s.ref or ''} {s.room_type.replace('_', ' ')} {s.type}".lower()
                if (floor_id is None or s.floor_id == floor_id) and all(w in hay for w in words):
                    out.append({"kind": "space", **self._space_brief(s)})
        if kind in ("any", "asset"):
            for a in b.assets.values():
                hay = f"{a.name} {a.type.replace('_', ' ')} {a.category}".lower()
                if (floor_id is None or a.floor_id == floor_id) and all(w in hay for w in words):
                    s = b.spaces[a.space_id]
                    out.append({"kind": "asset", "id": a.id, "name": a.name, "type": a.type,
                                "space_id": s.id, "space": s.name, "floor": b.floors[s.floor_id].name,
                                "position": [round(v, 2) for v in a.position]})
        return {"count": len(out), "results": out[:40], "truncated": len(out) > 40}

    @tool("Everything about one space: type, floor, area, centre, doors (to where, width, step-free), "
          "stairs/lifts, things inside, rooms sharing a wall, rooms above and below.",
          space={**S, "required": True})
    def describe_space(self, space: str) -> dict:
        b = self.b
        s = b.spaces[self.resolve_space(space)]
        doors = []
        for oid in s.opening_ids:
            o = b.openings[oid]
            other = o.other_side(s.id)
            doors.append({"id": oid, "kind": o.kind, "to": "outside" if other == OUTSIDE else b.spaces[other].name,
                          "to_id": other, "width_m": o.width_m, "step_free": o.step_free,
                          "closed": b.is_closed(oid)})
        things = Counter(b.assets[a].type for a in s.asset_ids)
        return {**self._space_brief(s), "type": s.type, "room_type": s.room_type,
                "area_m2": round(s.area_m2, 2), "centroid": [round(v, 2) for v in s.centroid],
                "doors": doors,
                "stairs_and_lifts": [{"id": c, "name": b.connectors[c].name, "kind": b.connectors[c].kind}
                                     for c in s.stop_ids],
                "things": dict(things.most_common()),
                "shares_wall_with": [{"name": o.name, "id": o.id, "wall_m": round(l, 1)}
                                     for o, l in A.wall_neighbours(b, s.id)],
                "above": [o.name for o, _ in A.stacked(b, s.id, "above", 1)],
                "below": [o.name for o, _ in A.stacked(b, s.id, "below", 1)],
                "closed": b.is_closed(s.id), "production_bay": s.is_production_bay}

    @tool("List spaces filtered by floor and/or room type, with areas, sorted largest first. "
          "Also returns the total area. Use for 'largest kitchen on floor 1' or 'total office area'.",
          floor=S, room_type={**S, "description": "e.g. office, kitchen, generic_room, production_zone"},
          limit={"type": "integer"})
    def list_rooms(self, floor: str | int | None = None, room_type: str | None = None, limit: int = 25) -> dict:
        floor_id = self._floor_id(floor)
        rooms = sorted(A.rooms_of_type(self.b, floor_id, room_type), key=lambda s: -s.area_m2)
        return {"count": len(rooms), "total_area_m2": round(sum(s.area_m2 for s in rooms), 2),
                "rooms": [{**self._space_brief(s), "room_type": s.room_type, "area_m2": round(s.area_m2, 2)}
                          for s in rooms[:limit]],
                "room_types_available": sorted({s.room_type for s in self.b.spaces.values()}) if not rooms else None}

    @tool("Geometry relations of a room: which spaces are directly above/below it (largest overlap), "
          "which rooms share at least 1 m of wall with it, and straight-line 3D distance to another room "
          "(centroids at floor elevation).",
          space={**S, "required": True}, other={**S, "description": "second room for distance_3d"})
    def spatial(self, space: str, other: str | None = None) -> dict:
        b = self.b
        sid = self.resolve_space(space)
        out = {"space": self._space_brief(b.spaces[sid]),
               "above": [{"name": o.name, "id": o.id, "overlap_m2": round(a, 1)} for o, a in A.stacked(b, sid, "above")],
               "below": [{"name": o.name, "id": o.id, "overlap_m2": round(a, 1)} for o, a in A.stacked(b, sid, "below")],
               "shares_wall_with": [{"name": o.name, "id": o.id, "wall_m": round(l, 2)} for o, l in A.wall_neighbours(b, sid)]}
        out["shared_wall_count"] = len(out["shares_wall_with"])
        if other:
            oid = self.resolve_space(other)
            out["distance_3d_m"] = round(A.straight_line_3d(b, sid, oid), 2)
        return out

    @tool("Inside one space: its doors, stair/lift stops, centre point and things, with walking "
          "distances between doors. Use for directions inside large halls.",
          space={**S, "required": True})
    def room_interior(self, space: str) -> dict:
        sid = self.resolve_space(space)
        it = self.nav.interior(sid)
        return {"space": self._space_brief(self.b.spaces[sid]),
                "waypoints": [{"kind": w.kind, "id": w.id, "label": w.label,
                               "point": [round(v, 2) for v in w.point]} for w in it.waypoints()][:60],
                "door_to_door_m": {f"{a} -> {c}": d for (a, c), d in list(it.door_matrix().items())[:80]}}

    # ==========================================================================================
    # Tools: moving
    # ==========================================================================================
    @tool("Shortest route between two places (rooms, room numbers or things). Returns the ordered "
          "spaces, length in metres and followable directions. Add `via` for a multi-stop tour in order. "
          "If no route exists under the rules, says so and why.",
          start={**S, "required": True}, goal={**S, "required": True},
          via={"type": "array", "items": S}, profile=PROFILE, min_width=N,
          avoid={"type": "array", "items": S, "description": "lifts/stairs/doors/rooms closed for this route only"})
    def route(self, start: str, goal: str, via: list[str] | None = None, profile: str = "walk",
              min_width: float | None = None, avoid: list[str] | None = None) -> dict:
        prof = self._profile(profile, min_width, avoid)
        stops = [self.resolve_space(start), *[self.resolve_space(v) for v in via or []], self.resolve_space(goal)]
        r, extra = self._scored(stops, prof)
        out = r.to_dict(self.b)
        out.update(extra)
        if r.reachable:
            out["directions"] = self.directions(r)
        else:
            out["explain"] = self._why_unreachable(stops, prof)
        return out

    def _scored(self, stops: list[str], prof: Profile) -> tuple[Route, dict]:
        """Strict rules, one cost model, then validation against the original request; a route the
        checker would reject is never returned. scoring.robust_route stays available for comparison."""
        def solve(p: Profile) -> Route:
            return self.nav.tour(stops, p) if len(stops) > 2 else self.nav.route(stops[0], stops[1], p)

        # Indoors first (stricter, never looser): outside may not count as a step for the checker.
        r = solve(replace(prof, allow_outside=False)) if prof.allow_outside else solve(prof)
        if not r.reachable and prof.allow_outside:
            r = solve(prof)
        r.profile = prof.name
        problems = scoring.validate(self.b, r.spaces, prof) if r.reachable else []
        if problems:
            return Route(False, stops[0], stops[-1], profile=prof.name,
                         reason="route failed validation: " + "; ".join(problems[:3])), {}
        return r, ({"uses_outside": OUTSIDE in r.spaces} if r.reachable else {})

    @tool("Check a route given as an ordered list of spaces exactly like the organizers' checker: is every "
          "step a real door/opening/stair/lift that the rules allow, and how long is it by their distance rule?",
          spaces={"type": "array", "items": S, "required": True}, profile=PROFILE, min_width=N,
          avoid={"type": "array", "items": S})
    def check_route(self, spaces: list[str], profile: str = "walk", min_width: float | None = None,
                    avoid: list[str] | None = None) -> dict:
        prof = self._profile(profile, min_width, avoid)
        ids = [s if s == OUTSIDE else self.resolve_space(s) for s in spaces]
        problems = scoring.validate(self.b, ids, prof)
        length = None if problems else scoring.measure(self.nav, ids, prof)
        return {"valid": not problems, "problems": problems[:20],
                "length_m": round(length, 2) if length is not None and length < math.inf else None}

    @tool("All rooms reachable from a start under the rules (e.g. a wheelchair user after a lift closure, "
          "or a 1.1 m trolley that needs wide doors). Counts rooms (not corridors, halls, stairs, lifts), "
          "optionally on one floor.",
          start={**S, "required": True}, floor=S, profile=PROFILE, min_width=N,
          avoid={"type": "array", "items": S})
    def reachable(self, start: str, floor: str | int | None = None, profile: str = "walk",
                  min_width: float | None = None, avoid: list[str] | None = None) -> dict:
        prof = self._profile(profile, min_width, avoid)
        sid = self.resolve_space(start)
        rooms = A.reachable_rooms(self.nav, sid, self._floor_id(floor), prof)
        return {"count": len(rooms), "profile": prof.name,
                "rooms": [s.name for s in sorted(rooms, key=lambda s: s.name)][:80]}

    @tool("Nearest room (by walking) that holds a thing type or is of a room type, e.g. the closest "
          "toilet or the closest room with a fire_extinguisher.",
          start={**S, "required": True}, asset_type=S, room_type=S, profile=PROFILE)
    def nearest(self, start: str, asset_type: str | None = None, room_type: str | None = None,
                profile: str = "walk") -> dict:
        b = self.b
        sid = self.resolve_space(start)
        if asset_type:
            t = asset_type.strip().lower().replace(" ", "_")
            cands = A.spaces_with_asset(b, t)
        elif room_type:
            cands = [s.id for s in A.rooms_of_type(b, None, room_type.strip().lower().replace(" ", "_"))]
        else:
            raise ToolError("need asset_type or room_type")
        if not cands:
            raise ToolError("not_found", hint="no such type here",
                            asset_types=sorted({a.type for a in b.assets.values()})[:80])
        best, d = self.nav.nearest(sid, cands, self._profile(profile))
        if best is None:
            return {"reachable": False}
        return {"reachable": True, "space": self._space_brief(b.spaces[best]), "length_m": round(d, 2)}

    @tool("Walking distance from rooms to the nearest entrance (out of the building).",
          space={**S, "required": True}, profile=PROFILE)
    def distance_to_exit(self, space: str, profile: str = "walk") -> dict:
        sid = self.resolve_space(space)
        d = self.nav.distances_to_exit(self._profile(profile), [sid]).get(sid)
        return {"space": self._space_brief(self.b.spaces[sid]), "reachable": d is not None,
                "length_m": round(d, 2) if d is not None else None}

    # ==========================================================================================
    # Tools: changing the building
    # ==========================================================================================
    @tool("Close or reopen a lift, staircase, door or room (lasting until reopened). Then compare routes "
          "to explain what changes.", element={**S, "required": True}, closed=B)
    def set_closed(self, element: str, closed: bool = True) -> dict:
        eid = self._element(element)
        self.b.set_closed(eid, closed)
        return {"element": eid, "closed": closed, "all_closed": sorted(self.b.closed)}

    @tool("Add a door between two neighbouring spaces in the middle of their shared wall.",
          first={**S, "required": True}, second={**S, "required": True}, width_m=N, step_free=B)
    def add_door(self, first: str, second: str, width_m: float | None = None, step_free: bool = True) -> dict:
        o = self.builder.add_door(self.resolve_space(first), self.resolve_space(second), None, width_m,
                                  step_free=step_free)
        return {"added": o.id, "position": [round(v, 2) for v in o.position], "width_m": o.width_m}

    @tool("What if a door were added between two spaces: how many rooms on a floor would get a shorter "
          "walk out of the building (by more than min_gain_m)? The building is left unchanged.",
          first={**S, "required": True}, second={**S, "required": True}, floor={**S, "required": True},
          min_gain_m=N)
    def what_if_door(self, first: str, second: str, floor: str | int, min_gain_m: float = 1.0) -> dict:
        r = A.what_if_door(self.nav, self.resolve_space(first), self.resolve_space(second),
                           self._floor_id(floor), min_gain_m)
        return {"count": r["count"], "improved": [self.b.spaces[s].name for s in r["improved"]][:60],
                "door_position": [round(v, 2) for v in r["door_position"]]}

    @tool("Create a thing (furniture, fixture, machine, equipment) in a room. Without x/y it is placed "
          "at the room's anchor point; x/y (metres, building frame) must lie inside the room.",
          name={**S, "required": True}, type={**S, "required": True, "description": "snake_case, e.g. cnc_mill, desk"},
          space={**S, "required": True},
          category={"type": "string", "enum": ["furniture", "fixture", "machine", "equipment"]},
          x=N, y=N, width_m=N, depth_m=N, rotation_deg=N)
    def add_asset(self, name: str, type: str, space: str, category: str = "equipment", x: float | None = None,
                  y: float | None = None, width_m: float = 0.6, depth_m: float = 0.6, rotation_deg: float = 0) -> dict:
        sid = self.resolve_space(space)
        a = self.builder.add_asset(sid, name, type, category, self._position_in(sid, x, y))
        a.footprint = {"width_m": width_m, "depth_m": depth_m, "rotation_deg": rotation_deg}
        return {"added": a.id, "name": a.name, "in": self._space_brief(self.b.spaces[sid]),
                "position": [round(v, 2) for v in a.position]}

    @tool("Move or re-position a thing: to another room ('the drill press is now in 0.21') and/or to an exact "
          "x/y point (metres) inside its room, optionally rotated.",
          asset={**S, "required": True}, to_space=S, x=N, y=N, rotation_deg=N)
    def move_asset(self, asset: str, to_space: str | None = None, x: float | None = None, y: float | None = None,
                   rotation_deg: float | None = None) -> dict:
        b = self.b
        aid = self._asset(asset)
        sid = self.resolve_space(to_space) if to_space else b.assets[aid].space_id
        keep = b.assets[aid].position if sid == b.assets[aid].space_id and x is None else None
        a = b.move_asset(aid, sid, self._position_in(sid, x, y) or keep)
        if rotation_deg is not None:
            a.footprint = {**a.footprint, "rotation_deg": rotation_deg}
        return {"asset": a.name, "now_in": self._space_brief(b.spaces[a.space_id]),
                "position": [round(v, 2) for v in a.position]}

    @tool("Delete a thing from the building.", asset={**S, "required": True})
    def remove_asset(self, asset: str) -> dict:
        a = self.b.remove_asset(self._asset(asset))
        return {"removed": a.id, "name": a.name, "was_in": self._space_brief(self.b.spaces[a.space_id])}

    def _asset(self, ref: str) -> str:
        b = self.b
        aid = ref if ref in b.assets else next((a.id for a in b.assets.values() if a.name.lower() == ref.lower()), None)
        if aid is None:
            hits = self.find(ref, kind="asset")["results"]
            if len(hits) != 1:
                raise ToolError("ambiguous" if hits else "not_found", reference=ref, candidates=hits[:12])
            aid = hits[0]["id"]
        return aid

    def _position_in(self, space_id: str, x: float | None, y: float | None) -> tuple[float, float] | None:
        if x is None and y is None:
            return None
        if x is None or y is None:
            raise ToolError("need_both_x_and_y")
        s = self.b.spaces[space_id]
        if not s.polygon.contains(ShapelyPoint(x, y)):
            minx, miny, maxx, maxy = s.polygon.bounds
            raise ToolError("outside_room", space=s.name, anchor=[round(v, 2) for v in s.anchor],
                            bounds=[round(v, 2) for v in (minx, miny, maxx, maxy)])
        return (float(x), float(y))

    @tool("Create a floor (for building a new building from plans).", level={"type": "integer", "required": True},
          name=S, elevation_m=N, height_m=N)
    def add_floor(self, level: int, name: str | None = None, elevation_m: float | None = None,
                  height_m: float = 3.5) -> dict:
        f = self.builder.add_floor(level, name, elevation_m, height_m)
        return {"id": f.id, "name": f.name, "elevation_m": f.elevation_m}

    @tool("Create a space from its outline (metres).", floor={**S, "required": True}, name={**S, "required": True},
          polygon={"type": "array", "items": {"type": "array", "items": N}, "required": True},
          type={"type": "string", "enum": ["room", "corridor", "stair", "lift", "hall", "outdoor"]},
          room_type=S, ref=S)
    def add_space(self, floor: str | int, name: str, polygon: list, type: str = "room",
                  room_type: str = "generic_room", ref: str | None = None) -> dict:
        s = self.builder.add_space(self._floor_id(floor), name, polygon, type, room_type, ref)
        return {"id": s.id, "name": s.name, "area_m2": round(s.area_m2, 2)}

    @tool("Add an entrance from outside into a space at a point on its outer wall.",
          space={**S, "required": True}, x={**N, "required": True}, y={**N, "required": True},
          width_m=N, step_free=B)
    def add_entrance(self, space: str, x: float, y: float, width_m: float = 1.8, step_free: bool = True) -> dict:
        o = self.builder.add_door(OUTSIDE, self.resolve_space(space), (x, y), width_m, step_free=step_free)
        return {"added": o.id}

    @tool("Connect spaces on different floors with stairs or a lift.",
          kind={"type": "string", "enum": ["stairs", "lift"], "required": True}, name={**S, "required": True},
          spaces={"type": "array", "items": S, "required": True})
    def add_connector(self, kind: str, name: str, spaces: list[str]) -> dict:
        c = self.builder.add_connector(kind, name, [self.resolve_space(s) for s in spaces])
        return {"id": c.id, "floors": [self.b.floors[f].name for f, _ in c.stops]}

    @tool("Check the building for problems: unreachable rooms, doors off walls, overlaps, no entrance.")
    def validate(self) -> dict:
        issues = self.builder.validate()
        return {"ok": not any(i["severity"] == "error" for i in issues), "issues": issues[:60],
                "issue_count": len(issues)}

    @tool("Save the (edited) building as building.json.", path={**S, "required": True})
    def save(self, path: str) -> dict:
        self.b.save(path)
        return {"saved": path}

    # ==========================================================================================
    # Helpers
    # ==========================================================================================
    def _space_brief(self, s: Space) -> dict:
        return {"id": s.id, "name": s.name, "floor": self.b.floors[s.floor_id].name}

    def directions(self, r: Route) -> list[str]:
        """One line per room change or floor change, with metres walked and a landmark when useful."""
        b, out = self.b, []
        legs = r.legs or [r]
        for leg in legs:
            out.append(f"Start in {b.spaces[leg.start].name} ({b.floor_of(leg.start).name}).")
            walked, cur = 0.0, leg.start
            for st in leg.steps:
                if st.kind == "walk" and st.space_id != cur:
                    s = b.spaces[st.space_id]
                    mark = self._landmark(s, st.b) if s.type == "hall" else ""
                    if cur in b.spaces and b.spaces[cur].type != "lift" or cur == OUTSIDE:
                        out.append(f"After about {walked:.0f} m go into {s.name}{mark}.")
                    else:
                        out.append(f"Step out into {s.name}{mark}.")
                    walked, cur = 0.0, st.space_id
                elif st.kind == "outside" and cur != OUTSIDE:
                    out.append(f"After about {walked:.0f} m leave the building through the entrance.")
                    walked, cur = 0.0, OUTSIDE
                elif st.kind == "stairs":
                    out.append(f"Take the {b.connectors[st.element_id].name} to {b.floor_of(st.space_id).name}.")
                    walked, cur = 0.0, st.space_id
                elif st.kind == "lift_ride":
                    if out and out[-1].startswith("Ride the"):
                        out.pop()
                    out.append(f"Ride the {b.connectors[st.element_id].name} to {b.floor_of(st.space_id).name}.")
                    walked, cur = 0.0, st.space_id
                if st.kind in ("walk", "outside"):
                    walked += st.length_m
            out.append(f"Arrive in {b.spaces[leg.goal].name} after about {walked:.0f} m. Total {leg.length_m:.0f} m.")
        return out

    def _landmark(self, s: Space, near) -> str:
        if near is None or not s.asset_ids:
            return ""
        machines = [self.b.assets[a] for a in s.asset_ids if self.b.assets[a].category == "machine"]
        if not machines:
            return ""
        m = min(machines, key=lambda a: math.dist(a.position, near))
        return f" (towards the {m.name})" if math.dist(m.position, near) < 15 else ""

    def _why_unreachable(self, stops: list[str], prof: Profile) -> str:
        b = self.b
        free = self.nav.tour(stops, WALK) if len(stops) > 2 else self.nav.route(stops[0], stops[-1], WALK)
        if not free.reachable:
            return "Not connected at all with the current closures: " + ", ".join(sorted(b.closed) or ["none"])
        blockers = []
        for el in free.elements:
            if el in b.connectors and not prof.connector_ok(b.connectors[el]):
                blockers.append(f"{b.connectors[el].name} ({b.connectors[el].kind})")
            elif el in b.openings and not prof.opening_ok(b.openings[el]):
                o = b.openings[el]
                blockers.append(f"{o.kind} {o.id} ({o.width_m} m{'' if o.step_free else ', steps'})")
        for sid in free.spaces[1:-1]:
            if sid in b.spaces and not prof.space_ok(b.spaces[sid]):
                blockers.append(f"{b.spaces[sid].name} (closed or not allowed for {prof.name})")
        hint = f" The normal route needs: {', '.join(dict.fromkeys(blockers))}." if blockers else ""
        return f"No route for profile '{prof.name}'.{hint}"


def _split_floor(ref: str) -> tuple[str, str | None]:
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", ref)
    return (m.group(1), m.group(2)) if m else (ref, None)


def _jsonable(o: Any):
    if isinstance(o, float) and (math.isinf(o) or math.isnan(o)):
        return None
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    return str(o)

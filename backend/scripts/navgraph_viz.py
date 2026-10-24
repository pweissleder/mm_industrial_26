"""Export one building's navgraph (every layer, as the real code computes it) into the
interactive viewer navgraph_viz.html.

    python scripts/navgraph_viz.py data/datasets/proc-factory-dev-003 out.html

The viewer re-runs NavGraph's Dijkstra in the browser on the exported L2 edge weights, so you
can step through the search. Python reference routes are embedded and checked on load."""

import itertools
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from navgraph import Building, NavGraph, OrganizerCost, rules  # noqa: E402
from navgraph.model import OUTSIDE  # noqa: E402
from navgraph.rules import is_forklift_space  # noqa: E402

TEMPLATE = Path(__file__).with_name("navgraph_viz.html")


def r2(p):
    return [round(p[0], 2), round(p[1], 2)]


def export(path: str) -> dict:
    b = Building.load(path)
    cost = OrganizerCost()
    nav = NavGraph(b, cost)
    g = nav.rooms.g

    spaces = []
    for s in b.spaces.values():
        area = cost.area(s)
        pts = {"A": s.anchor, **{oid: b.openings[oid].position for oid in s.opening_ids}}
        walk = {}
        for (ka, pa), (kb, pb) in itertools.combinations(pts.items(), 2):
            w = cost.walk(s, pa, pb)
            if math.isfinite(w):   # inf = no walk inside this space between the two points
                walk[f"{ka}|{kb}"] = [round(w, 4), [r2(p) for p in cost.walk_path(s, pa, pb)]]
        spaces.append({
            "id": s.id, "floor": s.floor_id, "type": s.type, "room_type": s.room_type, "name": s.name,
            "ref": s.ref, "polygon": [r2(p) for p in s.polygon.exterior.coords],
            "area": round(s.area_m2, 2), "anchor": r2(s.anchor), "centroid": r2(s.centroid),
            "bay": s.is_production_bay, "forklift": is_forklift_space(s), "circulation": s.is_circulation,
            "openings": s.opening_ids, "stops": s.stop_ids, "assets": s.asset_ids,
            "walkable": [[r2(p) for p in part.exterior.coords] for part in area.parts],
            "corners": [r2(p) for cs in area._corners for p in cs],
            "landing": {oid: r2(cost.land(s, b.openings[oid].position)) for oid in s.opening_ids},
            "walk": walk,
        })

    out = {
        "building": {"id": b.id, "name": b.name},
        "cost": {"name": cost.name, "label": cost.label(), "clearance_m": cost.clearance_m,
                 "door_landing_m": cost.door_landing_m, "stairs_per_m": cost.stairs_per_m,
                 "lift_wait_m": cost.lift_wait_m, "lift_per_m": cost.lift_per_m},
        "floors": [vars(f) for f in b.floors_by_level()],
        "spaces": spaces,
        "openings": [{"id": o.id, "floor": o.floor_id, "spaces": list(o.space_ids), "kind": o.kind,
                      "pos": r2(o.position), "width": o.width_m, "step_free": o.step_free}
                     for o in b.openings.values()],
        "connectors": [{"id": c.id, "kind": c.kind, "name": c.name, "step_free": c.step_free,
                        "stops": [list(st) for st in c.stops]} for c in b.connectors.values()],
        "assets": [{"id": a.id, "name": a.name, "type": a.type, "category": a.category,
                    "space": a.space_id, "pos": r2(a.position)} for a in b.assets.values()],
        "l1_edges": [{"a": a, "b": c, "key": k, "kind": d["kind"], "element": d["element"]}
                     for a, c, k, d in g.edges(keys=True, data=True)],
        "outside": OUTSIDE,
    }

    # reference routes from the real router, so the viewer can prove it computes the same thing
    rng = random.Random(0)
    ids = sorted(b.spaces)
    halls = sorted(s.id for s in b.spaces.values() if s.type == "hall")
    refs = []
    for prof_name, prof, pool in (("walk", rules.WALK, ids), ("step_free", rules.STEP_FREE, ids),
                                  ("forklift", rules.FORKLIFT, halls)):
        for a, c in (rng.sample(pool, 2) for _ in range(30)):
            r = nav.route(a, c, prof)
            refs.append({"start": a, "goal": c, "profile": prof_name, "reachable": r.reachable,
                         "length_m": round(r.length_m, 4) if r.reachable else None, "spaces": r.spaces})
    out["reference_routes"] = refs
    return out


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    data = json.dumps(export(src), separators=(",", ":"))
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", data)
    Path(dst).write_text(html, encoding="utf-8")
    print(f"wrote {dst} ({len(html) / 1e6:.1f} MB)")

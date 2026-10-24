"""Answer every benchmark question in a split from its `inputs` and compare to the expected answer."""
import json, sys, math, collections, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from navgraph import Building, NavGraph, OrganizerCost, rules, analysis as A

def solve(nav, q):
    b, i, k = nav.b, q["inputs"], q["kind"]
    if k == "floor_area_total": return A.total_area(b, i["floor_id"], i["room_type"])
    if k == "largest_room": s, area = A.largest_room(b, i["floor_id"], i["room_type"], 1)[0]; return (s.id, area)
    if k == "straight_line_3d": return A.straight_line_3d(b, i["first"], i["second"])
    if k == "room_stacked": return A.stacked(b, i["start"], i["direction"], 1)[0][0].id
    if k == "shared_walls_count": return len(A.wall_neighbours(b, i["start"]))
    if k == "errand": return A.errand(nav, i["start"], i["asset_type"], i["floor_id"], i["room_type"])["total_m"]
    if k == "wide_delivery_count": return len(A.reachable_rooms(nav, i["start"], i["floor_id"], rules.wide_load(i["min_width"])))
    if k == "step_free_reachable_count":
        return len(A.reachable_rooms(nav, i["start"], i["floor_id"], rules.STEP_FREE.with_closed(i["closed"])))
    if k == "what_if_door": return A.what_if_door(nav, i["first"], i["second"], i["floor_id"])["count"]
    raise ValueError(k)

def correct(q, got):
    a = q["answer"]
    if a["type"] == "space":
        sid = got[0] if isinstance(got, tuple) else got
        ok = sid == a["space_id"]
        if ok and "area" in a: ok = abs(got[1] - a["area"]["value"]) <= a["area"]["value"] * a["area"]["relative_tolerance"]
        return ok
    v = a["value"]; tol = max(abs(v) * a["relative_tolerance"], a["absolute_tolerance"])
    return abs(got - v) <= tol

if __name__ == "__main__":
    stats = collections.Counter(); bad = []
    t0 = time.time()
    for split in sys.argv[1:]:
        for qf in sorted(Path(split).glob("*/questions.jsonl")):
            lines = [l for l in qf.read_text().splitlines() if l.strip()]
            if not lines: continue
            nav = NavGraph(Building.load(qf.parent), OrganizerCost())
            for line in lines:
                q = json.loads(line)
                try: got = solve(nav, q); ok = correct(q, got)
                except Exception as e: got, ok = f"ERR {e!r}", False
                stats[(q["kind"], ok)] += 1
                if not ok: bad.append((q["id"], q["kind"], q["answer"].get("value", q["answer"].get("space_id")), got))
    kinds = sorted({k for k, _ in stats})
    for k in kinds: print(f"  {k:28s} {stats[(k,True)]:4d} / {stats[(k,True)]+stats[(k,False)]}")
    tot = sum(v for (k, ok), v in stats.items() if ok); n = sum(stats.values())
    print(f"  ALL {tot}/{n}  ({time.time()-t0:.0f}s)")
    for x in bad[:25]: print("  BAD", x)

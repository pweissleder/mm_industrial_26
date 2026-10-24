"""Reproduce every warm-up route in a split with OrganizerCost (the 1.1.0 route rules); report mismatches."""
import json, sys, glob, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from navgraph import Building, NavGraph, OrganizerCost, rules

split = Path(sys.argv[1])
n = ok = okpath = 0; bad = []
t0 = time.time()
for qf in sorted(split.glob("*/questions_basic.jsonl")):
    b = Building.load(qf.parent); nav = NavGraph(b, OrganizerCost())
    for line in qf.read_text().splitlines():
        q = json.loads(line); a = q["answer"]; k = q["kind"]
        if k not in ("route", "route_step_free", "route_with_closure", "tour"):
            continue
        prof = rules.STEP_FREE if k == "route_step_free" else rules.WALK
        if a.get("closed_element_id"):
            prof = prof.with_closed(a["closed_element_id"])
        if k == "tour":
            r = nav.tour(a["stops"], prof)
        else:
            if "closed_element_id" not in a and k == "route_with_closure":
                pass
            r = nav.route(a["from_space_id"], a["to_space_id"], prof)
        n += 1
        if a.get("reachable") is False or "optimal_length_m" not in a:
            good = not r.reachable
            exp = "unreachable"
        else:
            exp = a["optimal_length_m"]; good = r.reachable and abs(r.length_m - exp) <= 0.011
            if good and k != "tour" and r.spaces == a["space_path"]:
                okpath += 1
        ok += good
        if not good:
            bad.append((q["id"], k, exp, round(r.length_m, 2) if r.reachable else "unreachable", r.reason))
print(f"{ok}/{n} lengths match; {okpath} identical space paths; {time.time()-t0:.1f}s")
for x in bad[:20]: print("  BAD", x)

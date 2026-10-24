"""measure() must equal the router's length on its own path; robust routes must be valid."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from navgraph import Building, NavGraph, OrganizerCost, DatasetCost, rules
from navgraph.scoring import measure, robust_route

n = bad = 0; worst = []
for qf in sorted(Path(sys.argv[1]).glob("*/questions_basic.jsonl")):
    b = Building.load(qf.parent)
    for cm in (DatasetCost(), OrganizerCost()):
        nav = NavGraph(b, cm)
        for line in qf.read_text().splitlines():
            q = json.loads(line); a = q["answer"]
            if q["kind"] not in ("route", "route_step_free", "route_with_closure"):
                continue
            p = rules.STEP_FREE if q["kind"] == "route_step_free" else rules.WALK
            if a.get("closed_element_id"):
                p = p.with_closed(a["closed_element_id"])
            r = nav.route(a["from_space_id"], a["to_space_id"], p)
            if not r.reachable:
                continue
            n += 1; bad += abs(measure(nav, r.spaces, p) - r.length_m) > 0.01
            if isinstance(cm, OrganizerCost):
                sr = robust_route(nav, a["from_space_id"], a["to_space_id"], p)
                assert not sr.problems, (q["id"], sr.problems)
                worst.append(sr.worst_score)
print(f"measure == router: {n - bad}/{n}; robust routes valid: {len(worst)}; mean worst-case score {sum(worst)/len(worst):.4f}")

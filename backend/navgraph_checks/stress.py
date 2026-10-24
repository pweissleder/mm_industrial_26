"""Stress test: every building in the given splits through every layer.

  python tests/stress.py data/train [data/dev data/test] --out stress.json

Per building: load, validate, graph sizes, full level-2 build time, warm-up routes and benchmark
answers (when the split has answers), organizer-metric routing (validity + timing) on the warm-up
pairs and on random pairs."""
import argparse, json, math, random, sys, time, tracemalloc, statistics
from collections import Counter
from multiprocessing import Pool
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import networkx as nx
from navgraph import Building, NavGraph, OrganizerCost, DatasetCost, rules
from navgraph.builder import BuildingBuilder
from navgraph.model import OUTSIDE
from navgraph.scoring import validate, measure, robust_route
from navgraph.geometry import _parts

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_benchmark import solve, correct  # noqa: E402


def run(bdir: str) -> dict:
    bdir = Path(bdir); res = {"id": bdir.name, "split": bdir.parent.name}
    try:
        t = time.perf_counter(); tracemalloc.start()
        b = Building.load(bdir); res["t_load"] = time.perf_counter() - t
        res.update(source=b.meta.get("source"), floors=len(b.floors), spaces=len(b.spaces), openings=len(b.openings),
                   connectors=len(b.connectors), assets=len(b.assets))
        # validation
        issues = BuildingBuilder(b).validate()
        res["issues"] = Counter(f"{i['severity']}: {_label(i)}" for i in issues)
        res["unreachable"] = sum(1 for i in issues if "cannot be reached" in i["problem"])
        res["bay_only"] = sum(1 for i in issues if "crossing another factory zone" in i["problem"])
        res["not_step_free"] = sum(1 for i in issues if "not reachable step-free" in i["problem"])
        # level 1
        nav = NavGraph(b, OrganizerCost()); g = nav.rooms.g
        res["l1_nodes"], res["l1_edges"] = g.number_of_nodes(), g.number_of_edges()
        res["l1_components"] = nx.number_connected_components(nx.Graph(g))
        res["parallel_edges"] = sum(1 for u, v in nx.Graph(g).edges() if g.number_of_edges(u, v) > 1)
        # geometry health
        thin = split = 0
        for s in b.spaces.values():
            shr = s.polygon.buffer(-0.15, join_style=2)
            n = len([p for p in _parts(shr) if p.area > 1e-4])
            thin += n == 0; split += n > 1
        res["thin_spaces"], res["split_spaces"] = thin, split
        # level 2: build every interior completely
        t = time.perf_counter(); pairs = 0; maxpts = 0
        for s in b.spaces.values():
            pts = [s.anchor] + [b.openings[o].position for o in s.opening_ids] + ([s.anchor] if s.stop_ids else [])
            maxpts = max(maxpts, len(pts))
            for i in range(len(pts)):
                for j in range(i + 1, len(pts)):
                    nav.cost.walk(s, pts[i], pts[j]); pairs += 1
        res["l2_pairs"], res["l2_max_points"], res["t_l2_build"] = pairs, maxpts, time.perf_counter() - t
        res["mem_mb"] = tracemalloc.get_traced_memory()[1] / 1e6; tracemalloc.stop()
        # warm-up routes (dataset metric must match to the cm) + organizer routing on the same pairs
        dnav = NavGraph(b, DatasetCost())
        wu = Counter(); readings = Counter(); org_t = []; org_valid = 0; org_n = 0; worst = []; outside = 0; noroute_agree = 0
        qf = bdir / "questions_basic.jsonl"
        for line in (qf.read_text().splitlines() if qf.exists() else []):
            q = json.loads(line); a = q["answer"]; k = q["kind"]
            if k not in ("route", "route_step_free", "route_with_closure", "tour"):
                continue
            p = rules.STEP_FREE if k == "route_step_free" else rules.WALK
            if a.get("closed_element_id"):
                p = p.with_closed(a["closed_element_id"])
            r = dnav.tour(a["stops"], p) if k == "tour" else dnav.route(a["from_space_id"], a["to_space_id"], p)
            exp = a.get("optimal_length_m")
            ok = (not r.reachable) if exp is None else (r.reachable and abs(r.length_m - exp) <= 0.011)
            wu["pass" if ok else "fail"] += 1
            legs = [(x["from_space_id"], x["to_space_id"]) for x in a["legs"]] if k == "tour" else [(a["from_space_id"], a["to_space_id"])]
            for s0, s1 in legs:
                t = time.perf_counter(); sr = robust_route(nav, s0, s1, p); org_t.append(time.perf_counter() - t)
                readings[sr.reading] += 1
                org_n += 1
                if sr.route.reachable:
                    org_valid += not validate(b, sr.route.spaces, p); worst.append(sr.worst_score); outside += sr.uses_outside
                else:
                    noroute_agree += exp is None
        res["warmup"] = dict(wu); res["readings"] = dict(readings)
        res["org_routes"], res["org_valid"], res["org_unreachable"] = org_n, org_valid, org_n - len(worst)
        res["org_worst_mean"] = statistics.mean(worst) if worst else None
        res["org_worst_min"] = min(worst) if worst else None
        res["org_outside"] = outside
        res["t_robust"] = org_t
        # random pairs, plain organizer route
        rng = random.Random(7); ids = sorted(b.spaces)
        rt = []; rv = 0; rr = 0
        for _ in range(25):
            s0, s1 = rng.sample(ids, 2)
            t = time.perf_counter(); r = nav.route(s0, s1, rules.WALK); rt.append(time.perf_counter() - t)
            if r.reachable:
                rr += 1; rv += not validate(b, r.spaces, rules.WALK)
                assert abs(measure(nav, r.spaces, rules.WALK) - r.length_m) < 0.02
        res["t_random"] = rt; res["rand_reachable"] = rr; res["rand_valid"] = rv
        # benchmark answers
        bq = bdir / "questions.jsonl"; bm = Counter()
        lines = [l for l in (bq.read_text().splitlines() if bq.exists() else []) if l.strip()]
        for line in lines:
            q = json.loads(line)
            if "answer" not in q:
                continue
            try:
                ok = correct(q, solve(dnav, q))
            except Exception:
                ok = False
            bm[q["kind"] + (":pass" if ok else ":fail")] += 1
        res["bench"] = dict(bm)
        res["ok"] = True
    except Exception as e:
        import traceback
        res["ok"] = False; res["error"] = repr(e); res["trace"] = traceback.format_exc()[-800:]
    return res


def _label(i):
    p = i["problem"]
    if "cannot be reached" in p: return "unreachable from entrances"
    if "crossing another factory zone" in p: return "only reachable through another factory zone"
    if "not reachable step-free" in p: return "room not reachable step-free"
    if "from the wall" in p: return "door off the wall"
    if "overlaps" in p: return "spaces overlap"
    if "no entrance" in p: return "no entrance"
    return p[:40]


def _kind(p):
    return "unreachable" if "cannot be reached" in p else "no entrance" if "no entrance" in p else p.split(":")[0][:40]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("splits", nargs="+"); ap.add_argument("--out", default="stress.json")
    ap.add_argument("--workers", type=int, default=8); a = ap.parse_args()
    dirs = [str(d) for s in a.splits for d in sorted(Path(s).iterdir()) if (d / "building.json").exists()]
    t = time.time()
    with Pool(a.workers) as pool:
        out = pool.map(run, dirs, chunksize=1)
    for r in out:
        r["issues"] = dict(r.get("issues", {}))
    Path(a.out).write_text(json.dumps(out))
    print(f"{len(out)} buildings in {time.time()-t:.0f}s; failures: {[r['id'] for r in out if not r['ok']]}")

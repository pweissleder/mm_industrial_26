"""API cross-section over building families (stdlib only; needs a running backend).

    python scripts/e2e/api_matrix.py [--base http://localhost:8000] [--per-family 2]
        [--out docs/reports/e2e_api_matrix.json]

Per building: ingest, state, search (room/asset/exit), official routes (room->room across floors
if any, room->exit door, room->asset, point->room), nearest exit, precision route (ok or a clean
refusal), scenario close/stale/reset, plus negative cases. Every check records pass/fail with the
request so failures are reproducible (fixed seed).
"""

import argparse
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request

SEED = 20261024


def call(base, method, path, body=None):
    req = urllib.request.Request(
        base + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode(errors="replace")


def family(dataset):
    return dataset.rsplit("-", 1)[0] if dataset.startswith("proc") else dataset.split("-")[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--out", default="docs/reports/e2e_api_matrix.json")
    ap.add_argument("--per-family", type=int, default=2)
    a = ap.parse_args()
    api = a.base + "/api"
    rnd = random.Random(SEED)
    datasets = call(api, "GET", "/datasets")[1]["datasets"]
    fams = {}
    for d in datasets:
        fams.setdefault(family(d), []).append(d)
    chosen = [d for f in sorted(fams) for d in sorted(fams[f])[: a.per_family]]
    checks = []

    def check(building, name, ok, request=None, detail=None):
        checks.append(
            {"building": building, "check": name, "pass": bool(ok), "request": request,
             "detail": detail}
        )

    def status_of(st, body):
        if isinstance(body, dict):
            if st == 200:
                return body.get("status")
            return (body.get("error") or {}).get("code") or body.get("detail")
        return body

    for ds in chosen:
        began = time.perf_counter()
        st, ing = call(api, "POST", "/ingestions", {"dataset": ds})
        check(ds, "ingest", st == 201, {"dataset": ds}, st)
        if st != 201:
            continue
        b = ing.get("building_id") or ds
        model = call(api, "GET", f"/buildings/{b}/model")[1]
        st, state = call(api, "GET", f"/nav/{b}/state")
        check(b, "state", st == 200 and state.get("revision") is not None, None,
              state.get("support") if isinstance(state, dict) else state)
        floors = {f["id"]: f["level"] for f in model["floors"]}
        rooms = [s for s in model["spaces"] if s["type"] == "room"] or model["spaces"]
        exits = [o for o in model["openings"] if "outside" in o["space_ids"]]

        r0 = rnd.choice(rooms)
        q = urllib.parse.quote(r0["name"])
        st, res = call(api, "GET", f"/nav/{b}/search?q={q}&limit=10")
        check(b, "search_room_by_name",
              st == 200 and any(x["id"] == r0["id"] for x in res["results"]), r0["name"])
        if model["assets"]:
            asset = rnd.choice(model["assets"])
            q = urllib.parse.quote(asset["name"])
            st, res = call(api, "GET", f"/nav/{b}/search?q={q}&kind=asset&limit=25")
            check(b, "search_asset",
                  st == 200 and any(x["id"] == asset["id"] for x in res["results"]),
                  asset["name"])
        st, res = call(api, "GET", f"/nav/{b}/search?q=exit&kind=exit&limit=5")
        check(b, "search_exit", st == 200 and bool(res["results"]) == bool(exits), "exit",
              len(res.get("results", [])) if isinstance(res, dict) else res)

        by_floor = {}
        for s in rooms:
            by_floor.setdefault(s["floor_id"], []).append(s)
        fl = sorted(by_floor, key=lambda f: floors[f])
        start = rnd.choice(by_floor[fl[-1]])
        target = rnd.choice(by_floor[fl[0]])
        req = {"mode": "official", "start": {"kind": "space", "id": start["id"]},
               "target": {"kind": "space", "id": target["id"]}}
        st, route = call(api, "POST", f"/nav/{b}/route", req)
        multi = start["floor_id"] != target["floor_id"]
        ok = st == 200 and route["status"] in ("ok", "unreachable")
        if ok and route["status"] == "ok":
            wp = route["waypoints"]
            seg_floors = {seg["floor_id"] for seg in route["path"]}
            ok = (
                wp[0]["kind"] == "start"
                and wp[-1]["kind"] == "target"
                and abs(wp[-1]["fraction"] - 1) < 1e-6
                and (not multi or any(t["kind"] in ("stairs", "lift") for t in route["transitions"]))
                and (not multi or {start["floor_id"], target["floor_id"]} <= seg_floors)
            )
        check(b, "route_room_room" + ("_multifloor" if multi else ""), ok, req,
              {"http": st, "status": status_of(st, route),
               "length_m": route.get("length_m") if isinstance(route, dict) else None})
        if exits:
            ex = rnd.choice(exits)
            req = {"mode": "official", "start": {"kind": "space", "id": start["id"]},
                   "target": {"kind": "opening", "id": ex["id"]}}
            st, route = call(api, "POST", f"/nav/{b}/route", req)
            ok = st == 200 and (
                route["status"] != "ok"
                or (
                    route["target"]["id"] == ex["id"]
                    and max(abs(p - q) for p, q in
                            zip(route["waypoints"][-1]["position"], ex["position"])) < 1e-6
                )
            )
            check(b, "route_to_exit_ends_at_door", ok, req,
                  {"http": st, "status": status_of(st, route)})
            st, near = call(api, "POST", f"/nav/{b}/nearest",
                            {"from": {"kind": "space", "id": start["id"]}, "category": "exit",
                             "limit": 3})
            costs = ([x["walk_cost_m"] for x in near.get("results", [])]
                     if isinstance(near, dict) else [])
            check(b, "nearest_exit_sorted", st == 200 and costs == sorted(costs), start["id"],
                  costs)
        if model["assets"]:
            asset = rnd.choice(model["assets"])
            req = {"mode": "official", "start": {"kind": "space", "id": start["id"]},
                   "target": {"kind": "asset", "id": asset["id"]}}
            st, route = call(api, "POST", f"/nav/{b}/route", req)
            check(b, "route_to_asset", st == 200 and route["status"] in ("ok", "unreachable"),
                  req, status_of(st, route))
        poly = target.get("polygon") or []
        if poly:
            cx = sum(p[0] for p in poly) / len(poly)
            cy = sum(p[1] for p in poly) / len(poly)
            req = {"mode": "official",
                   "start": {"kind": "point", "space_id": target["id"], "position": [cx, cy]},
                   "target": {"kind": "space", "id": start["id"]}}
            st, route = call(api, "POST", f"/nav/{b}/route", req)
            clean = (st == 200 and route["status"] in ("ok", "unreachable")) or (
                st == 422 and isinstance(route, dict) and "error" in route)
            check(b, "route_from_point", clean, req, {"http": st, "status": status_of(st, route)})
        other = rnd.choice(by_floor[fl[0]])
        req = {"mode": "precision", "start": {"kind": "space", "id": target["id"]},
               "target": {"kind": "space", "id": other["id"]}}
        st, route = call(api, "POST", f"/nav/{b}/route", req)
        clean = (st == 200 and route["status"] in ("ok", "unreachable", "unsupported", "blocked")) or (
            st == 422 and isinstance(route, dict) and "error" in route)
        check(b, "precision_ok_or_clean_refusal", clean, req,
              {"http": st, "status": status_of(st, route)})

        element = (model["vertical_connectors"][0]["id"] if model["vertical_connectors"]
                   else model["openings"][0]["id"])
        rev = state["revision"]
        st1, s1 = call(api, "POST", f"/nav/{b}/scenario",
                       {"action": "close", "element_id": element, "expected_revision": rev})
        st2, _ = call(api, "POST", f"/nav/{b}/scenario",
                      {"action": "open", "element_id": element, "expected_revision": rev})
        new_rev = s1.get("revision", -1) if isinstance(s1, dict) else -1
        st3, s3 = call(api, "POST", f"/nav/{b}/scenario",
                       {"action": "reset", "expected_revision": new_rev})
        check(b, "scenario_close_stale409_reset",
              st1 == 200 and element in s1["closed"] and st2 == 409 and st3 == 200
              and s3["closed"] == [], element, [st1, st2, st3])

        st, e = call(api, "POST", f"/nav/{b}/route",
                     {"mode": "official", "start": {"kind": "space", "id": "nope"},
                      "target": {"kind": "space", "id": target["id"]}})
        check(b, "negative_unknown_place_422",
              st == 422 and e["error"]["code"] in ("unknown_place", "invalid_input"), "nope", e)
        st, e = call(api, "POST", f"/nav/{b}/route",
                     {"mode": "teleport", "start": {"kind": "space", "id": start["id"]},
                      "target": {"kind": "space", "id": target["id"]}})
        check(b, "negative_bad_mode_422", st == 422, "teleport", st)
        check(b, "elapsed_s", True, None, round(time.perf_counter() - began, 1))
    st, _ = call(api, "GET", "/nav/does-not-exist/state")
    check("-", "negative_unknown_building_404", st == 404, None, st)
    passed = sum(c["pass"] for c in checks)
    out = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S"), "seed": SEED, "buildings": chosen,
           "passed": passed, "total": len(checks),
           "failures": [c for c in checks if not c["pass"]], "checks": checks}
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    print(f"{passed}/{len(checks)} checks passed over {len(chosen)} buildings -> {a.out}")
    for c in out["failures"]:
        print("FAIL", c["building"], c["check"], json.dumps(c["detail"])[:300])


if __name__ == "__main__":
    main()

"""Validate that a generated .blend reproduces the dataset geometry exactly.

Usage (--python-exit-code makes Blender return 1 when the script calls sys.exit(1)):
  /Applications/Blender.app/Contents/MacOS/Blender -b model.blend --python-exit-code 1 \
      --python validate_model.py -- --building <building_dir> [--report out.json]

Checks assets / spaces / openings against <building_dir>/building.json:
  existence + uniqueness (by custom property asset_id / space_id / opening_id), position,
  z vs floor elevation, rotation (assets), dimensions (assets), space polygon vertices.
Tolerances: 1 mm positions and dimensions, 0.1 deg rotation.
"""
import argparse
import json
import math
import os
import sys

import bpy

TOL_POS = 1e-3  # metres
TOL_DIM = 1e-3  # metres
TOL_ROT = 0.1  # degrees
Z_BAND = 0.05  # metres: world verts within this of the floor are space-polygon candidates


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(prog="validate_model")
    p.add_argument("--building", required=True, help="dir containing building.json")
    p.add_argument("--report", help="write full results as JSON here")
    return p.parse_args(argv)


def index_objects(prop, exclude=()):
    """Map custom-property value -> list of objects carrying it (skipping objects with any `exclude` prop)."""
    idx = {}
    for o in bpy.data.objects:
        if prop in o.keys() and not any(e in o.keys() for e in exclude):
            idx.setdefault(str(o[prop]), []).append(o)
    return idx


def point_in_polygon(pt, poly):
    x, y = pt
    inside = False
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def angle_diff_deg(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


class Category:
    def __init__(self, name, ids, objs_by_id, extra_ids_label):
        self.name, self.checked, self.failures, self.max_err = name, 0, [], {}
        self.missing = [i for i in ids if i not in objs_by_id]
        self.duplicates = {i: [o.name for o in objs_by_id[i]] for i in ids if len(objs_by_id.get(i, [])) > 1}
        self.extra = sorted(set(objs_by_id) - set(ids))
        for i in self.missing:
            self.failures.append({"id": i, "error": "missing object"})
        for i, names in self.duplicates.items():
            self.failures.append({"id": i, "error": f"duplicate objects: {names}"})
        for i in self.extra:
            self.failures.append({"id": i, "error": f"extra object with unknown {extra_ids_label}"})

    def record(self, id_, **metrics):
        """metrics: name -> (value, tolerance). Any value above tolerance is a failure."""
        self.checked += 1
        bad = {}
        for k, (v, tol) in metrics.items():
            self.max_err[k] = max(self.max_err.get(k, 0.0), v)
            if v > tol:
                bad[k] = round(v, 6)
        if bad:
            self.failures.append({"id": id_, "error": "out of tolerance", "metrics": bad})

    def fail(self, id_, msg):
        self.checked += 1
        self.failures.append({"id": id_, "error": msg})

    def as_dict(self):
        return {"checked": self.checked, "n_failures": len(self.failures), "max_error": self.max_err,
                "missing": self.missing, "duplicates": self.duplicates, "extra": self.extra,
                "failures": self.failures}


def check_assets(data, elev):
    objs = index_objects("asset_id")
    cat = Category("assets", [a["id"] for a in data["assets"]], objs, "asset_id")
    spaces = {s["id"]: s for s in data["spaces"]}
    warnings = []
    for a in data["assets"]:
        sp = spaces.get(a["space_id"])
        if sp and not point_in_polygon(a["position"], sp["polygon"]):
            warnings.append({"id": a["id"], "warning": "dataset position outside its space polygon",
                             "space_id": a["space_id"]})
        if len(objs.get(a["id"], [])) != 1:
            continue
        o = objs[a["id"]][0]
        t, fp = o.matrix_world.translation, a["footprint"]
        rot = math.degrees(o.rotation_euler.z)
        cat.record(a["id"],
                   pos=(math.hypot(t.x - a["position"][0], t.y - a["position"][1]), TOL_POS),
                   z=(abs(t.z - elev[a["floor_id"]]), TOL_POS),
                   rot_deg=(angle_diff_deg(rot, fp["rotation_deg"]), TOL_ROT),
                   width=(abs(o.dimensions.x - fp["width_m"]), TOL_DIM),
                   depth=(abs(o.dimensions.y - fp["depth_m"]), TOL_DIM))
    return cat, warnings


def check_spaces(data, elev):
    objs = index_objects("space_id", exclude=("asset_id",))  # assets also carry space_id
    cat = Category("spaces", [s["id"] for s in data["spaces"]], objs, "space_id")
    for s in data["spaces"]:
        if len(objs.get(s["id"], [])) != 1:
            continue
        o, z0, poly = objs[s["id"]][0], elev[s["floor_id"]], s["polygon"]
        if o.type != "MESH":
            cat.fail(s["id"], f"object type {o.type}, expected MESH")
            continue
        mw = o.matrix_world
        cand = {}  # unique world verts near floor level, keyed by rounded xy
        for v in o.data.vertices:
            w = mw @ v.co
            if abs(w.z - z0) <= Z_BAND:
                cand[(round(w.x, 4), round(w.y, 4))] = w
        if not cand:
            cat.fail(s["id"], f"no vertices within {Z_BAND} m of floor z={z0}")
            continue
        ws = list(cand.values())
        # dataset -> model, and model -> dataset (so extra/missing vertices both show up)
        fwd = [min(ws, key=lambda w: math.hypot(w.x - px, w.y - py)) for px, py in poly]
        err_xy = max(math.hypot(w.x - px, w.y - py) for w, (px, py) in zip(fwd, poly))
        err_back = max(min(math.hypot(w.x - px, w.y - py) for px, py in poly) for w in ws)
        cat.record(s["id"],
                   vert_xy=(max(err_xy, err_back), TOL_POS),
                   z=(max(abs(w.z - z0) for w in ws), TOL_POS),
                   vert_count_diff=(abs(len(ws) - len(poly)), 0))
    return cat


def check_openings(data, elev):
    objs = index_objects("opening_id")
    cat = Category("openings", [op["id"] for op in data["openings"]], objs, "opening_id")
    for op in data["openings"]:
        if len(objs.get(op["id"], [])) != 1:
            continue
        t = objs[op["id"]][0].matrix_world.translation
        cat.record(op["id"],
                   pos=(math.hypot(t.x - op["position"][0], t.y - op["position"][1]), TOL_POS),
                   z=(abs(t.z - elev[op["floor_id"]]), TOL_POS))
    return cat


def main():
    args = parse_args()
    with open(os.path.join(args.building, "building.json")) as f:
        data = json.load(f)
    bpy.context.view_layer.update()
    elev = {fl["id"]: fl["elevation_m"] for fl in data["floors"]}

    assets, warnings = check_assets(data, elev)
    cats = {"assets": assets, "spaces": check_spaces(data, elev), "openings": check_openings(data, elev)}

    print(f"\n{'category':<10} {'checked':>8} {'failures':>9} {'missing':>8} {'extra':>6} {'dup':>4}  max errors")
    for name, c in cats.items():
        errs = ", ".join(f"{k}={v:.2e}" for k, v in c.max_err.items())
        print(f"{name:<10} {c.checked:>8} {len(c.failures):>9} {len(c.missing):>8} {len(c.extra):>6} "
              f"{len(c.duplicates):>4}  {errs}")
    print(f"dataset warnings: {len(warnings)} asset position(s) outside their space polygon")
    for name, c in cats.items():
        if c.failures:
            print(f"\nFirst failures [{name}] (of {len(c.failures)}):")
            for fl in c.failures[:10]:
                print("  ", json.dumps(fl))
    for w in warnings[:10]:
        print("  warning:", json.dumps(w))

    total = sum(len(c.failures) for c in cats.values())
    if args.report:
        with open(args.report, "w") as f:
            json.dump({"ok": total == 0, "total_failures": total, "dataset_warnings": warnings,
                       "categories": {n: c.as_dict() for n, c in cats.items()}}, f, indent=2)
        print(f"\nreport written to {args.report}")
    print("\nRESULT:", "PASS" if total == 0 else f"FAIL ({total} failures)")
    if total:
        sys.exit(1)


main()

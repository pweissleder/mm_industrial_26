"""Build a to-scale Blender model from a hackathon building.json.

Usage:
  /Applications/Blender.app/Contents/MacOS/Blender -b --python blender/build_building.py -- \
      --building <data/.../building_dir> --out <model.blend> [--glb <model.glb>] [--render <dir>]

Conventions (1 Blender unit = 1 m, dataset x/y map 1:1 to Blender X/Y, Z up):
- A floor's walking surface is at z = elevation_m. Slabs extend SLAB below it.
- Space meshes carry the dataset polygon verbatim; walls are inset WALL_T into each room,
  so two adjacent rooms produce a 2*WALL_T wall centred on their shared edge.
- Walls rise to height_m - SLAB, but stop under the slab of any higher floor that starts inside
  the storey and covers that stretch of wall (double-height halls with a partial upper floor).
- Each opening cuts a gap of width_m into the walls of the spaces it joins (lintel above DOOR_H for
  doors/entrances; none for kind "opening", which has no door leaf).
- Hall zones are open floor: no wall between two halls or a hall and a corridor, only a floor marking.
- Every asset is a linked duplicate of ONE placeholder mesh (unit box, origin at bottom centre),
  scaled to width_m x depth_m x type height, rotated by rotation_deg about Z.
  Its origin sits exactly at (x, y, elevation_m).
"""
import argparse
import json
import math
import os
import sys

import bpy
from mathutils import Vector

SLAB = 0.15     # slab thickness below each floor surface
WALL_T = 0.08   # wall thickness per room side
DOOR_H = 2.1    # door head height
ON_EDGE = 0.05  # max distance (m) for an opening to count as lying on a wall edge

ASSET_HEIGHT = {  # assumed; the dataset only has footprints
    "desk": 0.75, "chair": 0.9, "office_chair": 1.1, "shelf": 2.0, "bookshelf": 2.0, "fire_extinguisher": 0.6,
    "meeting_table": 0.75, "dining_table": 0.75, "reception_desk": 1.1, "bench": 0.45, "workbench": 0.9,
    "inspection_bench": 0.9, "assembly_station": 1.2, "packing_station": 0.95, "tool_cabinet": 1.0,
    "filing_cabinet": 1.3, "locker": 1.8, "coat_rack": 1.8, "whiteboard": 2.0, "display_screen": 1.8,
    "plant": 1.2, "kitchen_counter": 0.9, "sink": 0.9, "stove": 0.9, "dishwasher": 0.85, "fridge": 1.8,
    "microwave": 0.3, "coffee_machine": 0.45, "vending_machine": 1.85, "toilet": 0.8, "hand_dryer": 0.3,
    "defibrillator": 0.4, "pallet_rack": 4.5, "pallet_jack": 1.2, "forklift": 2.2,
    "forklift_charging_station": 1.5, "conveyor": 0.9, "compressor": 1.6, "press_brake": 3.0,
    "3d_printer": 0.8, "industrial_3d_printer": 2.0, "coordinate_measuring_machine": 2.6,
}
CATEGORY_HEIGHT = {"furniture": 0.8, "fixture": 1.0, "machine": 1.8, "equipment": 1.2}

PALETTE = {
    "room": (0.93, 0.90, 0.83, 1), "hall": (0.90, 0.88, 0.80, 1),
    "corridor": (0.80, 0.80, 0.80, 1), "stair": (0.95, 0.62, 0.42, 1),
    "lift": (0.60, 0.70, 0.95, 1), "outdoor": (0.70, 0.85, 0.60, 1),
    "wall": (0.62, 0.63, 0.66, 1), "door": (0.55, 0.30, 0.10, 1),
    "entrance": (0.20, 0.65, 0.25, 1), "label": (0.10, 0.10, 0.10, 1),
    "connector": (0.90, 0.30, 0.10, 1), "marking": (0.95, 0.80, 0.10, 1),
    "furniture": (0.55, 0.45, 0.35, 1), "fixture": (0.25, 0.60, 0.60, 1),
    "machine": (0.20, 0.35, 0.80, 1), "equipment": (0.85, 0.12, 0.10, 1),
}


# ---------------------------------------------------------------- geometry helpers
def signed_area(poly):
    return 0.5 * sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
                     for i in range(len(poly)))


def centroid(poly):
    a = signed_area(poly)
    cx = cy = 0.0
    for i in range(len(poly)):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % len(poly)]
        f = x0 * y1 - x1 * y0
        cx += (x0 + x1) * f
        cy += (y0 + y1) * f
    return cx / (6 * a), cy / (6 * a)


def inside(pt, poly):
    """Even-odd point-in-polygon test."""
    x, y, hit = pt[0], pt[1], False
    for i in range(len(poly)):
        (x0, y0), (x1, y1) = poly[i - 1], poly[i]
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            hit = not hit
    return hit


def on_boundary(pt, poly, tol=ON_EDGE):
    return nearest_edge(poly, pt)[0] < tol


def ccw(poly):
    return poly if signed_area(poly) > 0 else poly[::-1]


def edge_frame(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    u = (dx / length, dy / length)
    return length, u, (-u[1], u[0])  # inward normal for a CCW polygon


def nearest_edge(poly, p):
    best = None
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        length, u, n = edge_frame(a, b)
        s = max(0.0, min(length, (p[0] - a[0]) * u[0] + (p[1] - a[1]) * u[1]))
        d = math.hypot(a[0] + u[0] * s - p[0], a[1] + u[1] * s - p[1])
        if best is None or d < best[0]:
            best = (d, u)
    return best


def subtract(interval, cuts):
    lo, hi = interval
    out = []
    for c0, c1 in sorted(cuts):
        if c0 > lo:
            out.append((lo, min(c0, hi)))
        lo = max(lo, c1)
    if lo < hi:
        out.append((lo, hi))
    return [(s0, s1) for s0, s1 in out if s1 - s0 > 0.01]


# ---------------------------------------------------------------- blender helpers
_materials = {}


def material(key):
    if key not in _materials:
        m = bpy.data.materials.new(f"M_{key}")
        m.diffuse_color = PALETTE[key]
        m.use_nodes = True
        m.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = PALETTE[key]
        m.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.8
        _materials[key] = m
    return _materials[key]


def collection(name, parent):
    c = bpy.data.collections.new(name)
    parent.children.link(c)
    return c


def mesh_object(name, verts, faces, coll, mat_key, props=None):
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    ob = bpy.data.objects.new(name, me)
    ob.data.materials.append(material(mat_key))
    for k, v in (props or {}).items():
        ob[k] = v
    coll.objects.link(ob)
    return ob


def add_box(verts, faces, a, u, n, s0, s1, t0, t1, z0, z1):
    """Append a box spanning [s0,s1] along u and [t0,t1] along n from point a."""
    base = len(verts)
    for z in (z0, z1):
        for s, t in ((s0, t0), (s1, t0), (s1, t1), (s0, t1)):
            verts.append((a[0] + u[0] * s + n[0] * t, a[1] + u[1] * s + n[1] * t, z))
    faces += [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    faces[-6:] = [tuple(base + i for i in f) for f in faces[-6:]]


def placeholder_mesh():
    """The one shared placeholder equipment mesh: unit box, origin at bottom centre."""
    v = [(x, y, z) for z in (0, 1) for x, y in ((-.5, -.5), (.5, -.5), (.5, .5), (-.5, .5))]
    f = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    me = bpy.data.meshes.new("PH_Equipment_Unit")
    me.from_pydata(v, [], f)
    me.materials.append(material("furniture"))
    me.update()
    return me


# ---------------------------------------------------------------- build
def build(b):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    root = collection(f"Building_{b['building']['id']}", scene.collection)
    root["name"] = b["building"]["name"]

    spaces = {s["id"]: s for s in b["spaces"]}
    floors = {f["id"]: f for f in b["floors"]}
    ph = placeholder_mesh()
    floor_colls = {}

    for fl in sorted(b["floors"], key=lambda f: f["level"]):
        z = fl["elevation_m"]
        full_top = z + fl["height_m"] - SLAB
        # floors that start inside this storey (e.g. an office level over part of a double-height hall)
        above = [(f["elevation_m"] - SLAB, [s["polygon"] for s in b["spaces"] if s["floor_id"] == f["id"]])
                 for f in b["floors"] if z < f["elevation_m"] < z + fl["height_m"]]

        def top_at(p):
            return min([full_top] + [e for e, polys in above if any(inside(p, q) for q in polys)])

        def wall_runs(a, u, nrm, s0, s1, z0):
            """Split [s0,s1] into runs with a constant wall top, sampled every 0.25 m at the wall centre."""
            k = max(1, math.ceil((s1 - s0) / 0.25))
            cuts = [s0 + (s1 - s0) * j / k for j in range(k + 1)]
            tops = [top_at((a[0] + u[0] * (c0 + c1) / 2 + nrm[0] * WALL_T / 2,
                            a[1] + u[1] * (c0 + c1) / 2 + nrm[1] * WALL_T / 2)) for c0, c1 in zip(cuts, cuts[1:])]
            j0 = 0
            for j in range(1, k + 1):
                if j == k or tops[j] != tops[j0]:
                    if tops[j0] > z0:
                        add_box(wv, wf, a, u, nrm, cuts[j0], cuts[j], 0, WALL_T, z0, tops[j0])
                    j0 = j
        fc = collection(f"Floor_{fl['level']}", root)
        fc["floor_id"], fc["elevation_m"] = fl["id"], z
        floor_colls[fl["id"]] = fc
        c_sp, c_wall, c_open, c_asset, c_lab = (collection(f"F{fl['level']}_{n}", fc)
                                                for n in ("Spaces", "Walls", "Openings", "Assets", "Labels"))
        f_spaces = [s for s in b["spaces"] if s["floor_id"] == fl["id"]]
        f_open = [o for o in b["openings"] if o["floor_id"] == fl["id"]]
        wv, wf, mv, mf = [], [], [], []
        open_types = {"hall", "corridor"}

        for s in f_spaces:
            poly = ccw(s["polygon"])
            n = len(poly)
            verts = [(x, y, z) for x, y in poly] + [(x, y, z - SLAB) for x, y in poly]
            faces = [tuple(range(n)), tuple(range(2 * n - 1, n - 1, -1))]
            faces += [(i, (i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
            key = s["type"] if s["type"] in PALETTE else "room"
            mesh_object(f"{s.get('ref') or s['name']} [{s['id'][-4:]}]", verts, [tuple(reversed(f)) for f in faces],
                        c_sp, key, {"space_id": s["id"], "space_name": s["name"], "space_type": s["type"],
                                    "room_type": s.get("room_type", ""), "floor_level": fl["level"],
                                    "area_m2": round(abs(signed_area(poly)), 3)})

            # walls inset into this room, with gaps for its openings
            mine = [o for o in f_open if s["id"] in o["space_ids"]]
            for i in range(n):
                a, c = poly[i], poly[(i + 1) % n]
                length, u, nrm = edge_frame(a, c)
                mid = ((a[0] + c[0]) / 2, (a[1] + c[1]) / 2)
                if s["type"] in open_types and any(
                        t["id"] != s["id"] and "hall" in (s["type"], t["type"]) and t["type"] in open_types
                        and on_boundary(mid, t["polygon"]) for t in f_spaces):
                    add_box(mv, mf, a, u, nrm, 0, length, 0, WALL_T, z, z + 0.01)  # painted zone line
                    continue
                cuts, lintels = [], []
                for o in mine:
                    px, py = o["position"][0] - a[0], o["position"][1] - a[1]
                    sv, dist = px * u[0] + py * u[1], abs(px * nrm[0] + py * nrm[1])
                    if dist < ON_EDGE and -ON_EDGE <= sv <= length + ON_EDGE:
                        cuts.append((sv - o["width_m"] / 2, sv + o["width_m"] / 2))
                        if o["kind"] != "opening":
                            lintels.append(cuts[-1])
                for s0, s1 in subtract((0.0, length), cuts):
                    wall_runs(a, u, nrm, s0, s1, z)
                for c0, c1 in lintels:  # lintel over each door
                    c0, c1 = max(c0, 0.0), min(c1, length)
                    if c1 > c0:
                        wall_runs(a, u, nrm, c0, c1, z + DOOR_H)

            # flat room label at the polygon centroid
            cu = bpy.data.curves.new(f"Label_{s['id']}", "FONT")
            cu.body, cu.size, cu.align_x, cu.align_y = s.get("ref") or s["name"], 0.6, "CENTER", "CENTER"
            lab = bpy.data.objects.new(f"Label {s.get('ref') or s['name']}", cu)
            lab.location = (*centroid(poly), z + 0.01)
            lab.data.materials.append(material("label"))
            c_lab.objects.link(lab)

        mesh_object(f"Walls F{fl['level']}", wv, wf, c_wall, "wall", {"floor_level": fl["level"]})
        if mv:
            mesh_object(f"Zone markings F{fl['level']}", mv, mf, c_wall, "marking", {"floor_level": fl["level"]})

        for o in f_open:
            host = next(spaces[i] for i in o["space_ids"] if i != "outside")
            _, u = nearest_edge(host["polygon"], o["position"])
            w = o["width_m"]
            # threshold strip across the full wall depth, origin at the opening position on the floor
            verts, faces = [], []
            add_box(verts, faces, (0, 0), (1, 0), (0, 1), -w / 2, w / 2, -WALL_T - 0.02, WALL_T + 0.02, 0, 0.02)
            ob = mesh_object(f"Opening {o['id'][-5:]}", verts, faces, c_open,
                             "entrance" if "outside" in o["space_ids"] else "door",
                             {"opening_id": o["id"], "kind": o["kind"], "width_m": w,
                              "space_ids": ",".join(o["space_ids"]), "step_free": o["step_free"]})
            ob.location = (o["position"][0], o["position"][1], z)
            ob.rotation_euler.z = math.atan2(u[1], u[0])

        for a in (a for a in b["assets"] if a["floor_id"] == fl["id"]):
            fp = a["footprint"]
            h = ASSET_HEIGHT.get(a["type"], CATEGORY_HEIGHT.get(a["category"], 1.0))
            ob = bpy.data.objects.new(a["name"], ph)  # linked duplicate of the single placeholder
            ob.location = (a["position"][0], a["position"][1], z)
            ob.rotation_euler.z = math.radians(fp["rotation_deg"])
            ob.scale = (fp["width_m"], fp["depth_m"], h)
            ob.material_slots[0].link = "OBJECT"
            ob.material_slots[0].material = material(a["category"] if a["category"] in PALETTE else "equipment")
            for k, v in {"asset_id": a["id"], "asset_type": a["type"], "category": a["category"],
                         "space_id": a["space_id"], "floor_level": fl["level"],
                         "width_m": fp["width_m"], "depth_m": fp["depth_m"], "height_m": h,
                         "rotation_deg": fp["rotation_deg"]}.items():
                ob[k] = v
            c_asset.objects.link(ob)

    # vertical connectors: a polyline through the stop centroids, mid-storey height
    c_vc = collection("Vertical_Connectors", root)
    for vc in b["vertical_connectors"]:
        cu = bpy.data.curves.new(vc["id"], "CURVE")
        cu.dimensions, cu.bevel_depth = "3D", 0.12
        sp = cu.splines.new("POLY")
        stops = [(spaces[s], floors[f]) for s, f in zip(vc["space_ids"], vc["floor_ids"])]
        sp.points.add(len(stops) - 1)
        for p, (s, f) in zip(sp.points, stops):
            p.co = (*centroid(ccw(s["polygon"])), f["elevation_m"] + 1.2, 1)
        ob = bpy.data.objects.new(f"{vc['kind'].title()} {vc['name']}", cu)
        ob.data.materials.append(material("connector"))
        ob["connector_id"], ob["kind"], ob["step_free"] = vc["id"], vc["kind"], vc["step_free"]
        c_vc.objects.link(ob)
    return floor_colls


# ---------------------------------------------------------------- render
def look_at(ob, target):
    ob.rotation_euler = (Vector(target) - ob.location).to_track_quat("-Z", "Y").to_euler()


def render_all(b, floor_colls, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    sh = scene.display.shading
    sh.light, sh.color_type, sh.show_cavity, sh.show_shadows = "FLAT", "MATERIAL", True, False
    sh.cavity_type = "BOTH"
    scene.world = bpy.data.worlds.new("World")
    scene.world.color = (1, 1, 1)
    scene.render.film_transparent = False
    scene.display_settings.display_device = "sRGB"
    scene.view_settings.view_transform = "Standard"

    xs = [p[0] for s in b["spaces"] for p in s["polygon"]]
    ys = [p[1] for s in b["spaces"] for p in s["polygon"]]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    zmin = min(f["elevation_m"] for f in b["floors"])
    zmax = max(f["elevation_m"] + f["height_m"] for f in b["floors"])

    cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    connectors = bpy.data.collections["Vertical_Connectors"]

    def shoot(name, visible):
        for fid, c in floor_colls.items():
            c.hide_render = fid not in visible
        connectors.hide_render = len(visible) < len(floor_colls)  # the polyline spans all floors
        scene.render.filepath = os.path.join(out_dir, name)
        bpy.ops.render.render(write_still=True)

    # top-down orthographic plans, same orientation as the dataset's floor_<level>.png
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = max(w, h) * 1.08
    scene.render.resolution_x = int(1400 * w / max(w, h))
    scene.render.resolution_y = int(1400 * h / max(w, h))
    for fl in b["floors"]:
        cam.location, cam.rotation_euler = (cx, cy, fl["elevation_m"] + 50), (0, 0, 0)
        shoot(f"plan_floor_{fl['level']}.png", {fl["id"]})

    # perspective views: each floor alone, then the whole building
    sh.light = "STUDIO"
    cam.data.type, cam.data.lens = "PERSP", 35
    scene.render.resolution_x, scene.render.resolution_y = 1600, 1100
    d = max(w, h)
    for fl in b["floors"]:
        cam.location = (cx + 0.55 * d, cy - 0.95 * d, fl["elevation_m"] + 0.85 * d)
        look_at(cam, (cx, cy, fl["elevation_m"]))
        shoot(f"view_floor_{fl['level']}.png", {fl["id"]})
    cam.location = (cx + 0.75 * d, cy - 1.3 * d, zmax + 0.8 * d)
    look_at(cam, (cx, cy, (zmin + zmax) / 2))
    shoot("view_building.png", set(floor_colls))
    for c in (*floor_colls.values(), connectors):
        c.hide_render = False


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--building", required=True, help="directory containing building.json")
    ap.add_argument("--out", required=True, help="output .blend path")
    ap.add_argument("--glb", help="optional glTF binary export (custom props -> extras)")
    ap.add_argument("--render", help="optional directory for plan / perspective renders")
    args = ap.parse_args(argv)

    with open(os.path.join(args.building, "building.json")) as fh:
        b = json.load(fh)
    floor_colls = build(b)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    if args.render:
        render_all(b, floor_colls, args.render)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.out))
    if args.glb:
        bpy.ops.export_scene.gltf(filepath=os.path.abspath(args.glb), export_format="GLB", export_extras=True)
    print(f"built {b['building']['id']}: {len(b['spaces'])} spaces, {len(b['openings'])} openings, "
          f"{len(b['assets'])} assets -> {args.out}")


main()

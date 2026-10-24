"""Model validator: re-reads an exported GLB and compares it with building.json.

The GLB is loaded from disk (never the in-memory scene). Node extras are read from the
GLB's own JSON chunk, geometry and world transforms through trimesh. Every failure is a
`Deviation` attributed to the stage that has to fix it: `floorplan` for spaces, walls,
openings and connectors, `equipment` for asset placement, `variant_creator` for a library
mesh that leaves its unit box. Data quirks that are not model errors go to `warnings`.
"""

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, Field
from shapely.geometry import Point, Polygon

from app.domain.building import BuildingFile
from app.pipeline.state import Deviation, Stage
from app.pipeline.tools.geo3d.floorplan import building_bounds, floor_node
from app.pipeline.tools.geo3d.frame import (
    angle_diff_deg,
    decompose_asset_matrix,
    from_gltf_matrix,
    from_gltf_points,
)
from app.pipeline.tools.geo3d.model import load_glb, read_gltf_json

POS_TOL = 1e-3  # m
ROT_TOL = 0.1  # deg
BOUNDS_MARGIN = 1.0  # m
UNIT_BOX_TOL = 1e-3
ENTITY_KINDS = ("space", "opening", "connector", "asset")
STATIC_KINDS = ("building", "floor", "walls", "hall_lines", "marker")
MAX_WARNINGS = 200


class ValidationReport(BaseModel):
    passed: bool
    checked: dict[str, int] = Field(default_factory=dict)
    max_error: dict[str, float] = Field(default_factory=dict)
    failures: list[Deviation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def summary(self) -> str:
        head = "PASS" if self.passed else f"FAIL ({len(self.failures)} failures)"
        errs = ", ".join(f"{k}={v:.2e}" for k, v in sorted(self.max_error.items()))
        return f"{head}; checked {self.checked}; max errors {errs}"


def _stage_for(kind: str | None) -> Stage:
    return "equipment" if kind == "asset" else "floorplan"


def _set_diff(a: np.ndarray, b: np.ndarray) -> float:
    """Symmetric max nearest-neighbour distance between two 2D point sets."""
    if len(a) == 0 or len(b) == 0:
        return math.inf
    d = np.linalg.norm(a[:, None, :] - b[None, :, :], axis=2)
    return float(max(d.min(axis=1).max(), d.min(axis=0).max()))


class _Checker:
    def __init__(self) -> None:
        self.failures: list[Deviation] = []
        self.warnings: list[str] = []
        self.n_warnings = 0
        self.max_error: dict[str, float] = defaultdict(float)
        self.checked: Counter[str] = Counter()

    def fail(self, stage: Stage, kind: str, entity_id: str | None, message: str) -> None:
        self.failures.append(
            Deviation(stage=stage, kind=kind, entity_id=entity_id, message=message)
        )

    def warn(self, message: str) -> None:
        self.n_warnings += 1
        if len(self.warnings) < MAX_WARNINGS:
            self.warnings.append(message)

    def measure(
        self, metric: str, err: float, tol: float, stage: Stage, entity_id: str, what: str
    ) -> None:
        if not math.isfinite(err):
            self.fail(stage, f"{metric}_invalid", entity_id, f"{what}: non-finite value")
            return
        self.max_error[metric] = max(self.max_error[metric], err)
        if err > tol:
            self.fail(stage, f"{metric}_mismatch", entity_id, f"{what}: error {err:.6g} > {tol}")


def validate_model(
    glb_path: Path, index_path: Path, building: BuildingFile, expect_assets: bool = True
) -> ValidationReport:
    ck = _Checker()
    gj = read_gltf_json(glb_path)
    scene = load_glb(glb_path)
    index: dict[str, dict[str, Any]] = json.loads(index_path.read_text())
    to_ds = from_gltf_matrix()

    # --- node inventory -----------------------------------------------------------------
    nodes = gj.get("nodes", [])
    names = [str(n.get("name", "")) for n in nodes]
    extras_by_name: dict[str, dict[str, Any]] = {}
    for n in nodes:
        extras_by_name[str(n.get("name", ""))] = dict(n.get("extras") or {})
    ck.checked["nodes"] = len(nodes)
    for name, count in Counter(names).items():
        if count > 1:
            kind = extras_by_name.get(name, {}).get("kind")
            ck.fail(_stage_for(kind), "duplicate_node", name, f"node {name!r} appears {count}x")
    parents: dict[str, str] = {}
    for n in nodes:
        for c in n.get("children", []) or []:
            if 0 <= c < len(nodes):
                parents[names[c]] = str(n.get("name", ""))

    expected: dict[str, tuple[str, str]] = {}  # node name -> (kind, floor_id or "")
    for s in building.spaces:
        expected[s.id] = ("space", s.floor_id)
    for o in building.openings:
        expected[o.id] = ("opening", o.floor_id)
    for v in building.vertical_connectors:
        expected[v.id] = ("connector", "")
    if expect_assets:
        for a in building.assets:
            expected[a.id] = ("asset", a.floor_id)

    for name, (kind, floor_id) in expected.items():
        ex = extras_by_name.get(name)
        if ex is None:
            ck.fail(_stage_for(kind), "missing_node", name, f"no {kind} node for {name}")
            continue
        if ex.get("kind") != kind:
            ck.fail(
                _stage_for(kind),
                "wrong_node_kind",
                name,
                f"node {name} has kind {ex.get('kind')!r}, expected {kind!r}",
            )
        if floor_id and parents.get(name) != floor_node(floor_id):
            ck.fail(
                _stage_for(kind),
                "wrong_parent",
                name,
                f"{kind} {name} hangs under {parents.get(name)!r}, expected {floor_node(floor_id)}",
            )
    for name, ex in extras_by_name.items():
        kind = ex.get("kind")
        if name not in index:
            ck.fail(_stage_for(kind), "unindexed_node", name, f"node {name} not in the index")
        elif index[name].get("kind") != kind:
            ck.fail(_stage_for(kind), "index_mismatch", name, "index extras differ from GLB")
        if kind in ENTITY_KINDS:
            if kind == "asset" and not expect_assets:
                ck.fail("floorplan", "unexpected_asset", name, "asset in a floorplan-only model")
            elif name not in expected:
                ck.fail(_stage_for(kind), "unknown_node", name, f"{kind} {name} not in building")
        elif kind not in STATIC_KINDS and name != "world":
            ck.fail("floorplan", "unknown_node", name, f"node {name} has unknown kind {kind!r}")
    for name in index:
        if name not in extras_by_name:
            kind = index[name].get("kind")
            ck.fail(_stage_for(kind), "index_orphan", name, f"index entry {name} has no node")

    # --- geometry sanity: finite, non-empty, inside the building box --------------------
    lo, hi = building_bounds(building)
    top_margin = BOUNDS_MARGIN
    if expect_assets:
        top_margin += max(
            [
                float(e.get("height_m", 0.0))
                for e in extras_by_name.values()
                if e.get("kind") == "asset"
            ]
            + [0.0]
        )
    geom_ok: dict[str, bool] = {}
    geom_corners: dict[str, np.ndarray] = {}
    owners: dict[str, str] = {}  # geometry -> one node using it (for attribution)
    world: dict[str, tuple[np.ndarray, str | None]] = {}
    for name in extras_by_name:
        if name not in scene.graph.nodes:
            continue
        matrix, geom_key = scene.graph.get(name)
        geom = None if geom_key is None else str(geom_key)
        world[name] = (np.asarray(matrix, dtype=np.float64), geom)
        if geom is not None:
            owners.setdefault(geom, name)
    for geom, mesh in scene.geometry.items():
        verts = np.asarray(getattr(mesh, "vertices", np.zeros((0, 3))), dtype=np.float64)
        faces = np.asarray(getattr(mesh, "faces", np.zeros((0, 3))))
        owner = owners.get(geom)
        okind = extras_by_name.get(owner or "", {}).get("kind")
        stage: Stage = "variant_creator" if geom.startswith("asset_type:") else _stage_for(okind)
        ck.checked["geometries"] += 1
        if len(verts) == 0 or len(faces) == 0:
            ck.fail(stage, "empty_mesh", owner or geom, f"geometry {geom} is empty")
            geom_ok[geom] = False
            continue
        if not np.isfinite(verts).all():
            ck.fail(stage, "non_finite_mesh", owner or geom, f"geometry {geom} has NaN/inf")
            geom_ok[geom] = False
            continue
        geom_ok[geom] = True
        b = np.asarray(mesh.bounds)
        geom_corners[geom] = np.array(
            [[x, y, z, 1.0] for x in b[:, 0] for y in b[:, 1] for z in b[:, 2]]
        )
        if geom.startswith(("asset_type:", "unit_opening:")):
            ds = from_gltf_points(verts)
            lo_u, hi_u = ds.min(axis=0), ds.max(axis=0)
            if (lo_u < np.array([-0.5, -0.5, 0.0]) - UNIT_BOX_TOL).any() or (
                hi_u > np.array([0.5, 0.5, 1.0]) + UNIT_BOX_TOL
            ).any():
                ck.fail(
                    stage,
                    "mesh_outside_unit_box",
                    geom,
                    f"{geom} spans {np.round(lo_u, 3).tolist()}..{np.round(hi_u, 3).tolist()}",
                )
    for name, (matrix, geom) in world.items():
        kind = extras_by_name[name].get("kind")
        if not np.isfinite(matrix).all():
            ck.fail(_stage_for(kind), "non_finite_transform", name, f"{name}: NaN/inf matrix")
            continue
        if geom is None or not geom_ok.get(geom) or geom not in geom_corners:
            continue
        pts = (to_ds @ matrix @ geom_corners[geom].T).T[:, :3]
        p_lo, p_hi = pts.min(axis=0), pts.max(axis=0)
        margin_lo = np.full(3, BOUNDS_MARGIN)
        margin_hi = np.array([BOUNDS_MARGIN, BOUNDS_MARGIN, top_margin])
        if (p_lo < lo - margin_lo).any() or (p_hi > hi + margin_hi).any():
            ck.fail(
                _stage_for(kind),
                "outside_building_bounds",
                name,
                f"{name} spans {np.round(p_lo, 2).tolist()}..{np.round(p_hi, 2).tolist()}, "
                f"building {np.round(lo, 2).tolist()}..{np.round(hi, 2).tolist()}",
            )

    floors = {f.id: f for f in building.floors}

    def node_geometry(name: str) -> tuple[np.ndarray, np.ndarray] | None:
        entry = world.get(name)
        if entry is None or entry[1] is None or not geom_ok.get(entry[1]):
            return None
        matrix, geom = entry
        verts = np.asarray(scene.geometry[geom].vertices, dtype=np.float64)
        hom = np.column_stack([verts, np.ones(len(verts))])
        return matrix, (to_ds @ matrix @ hom.T).T[:, :3]

    # --- spaces: polygon verbatim at z = elevation --------------------------------------
    for s in building.spaces:
        got = node_geometry(s.id)
        floor = floors.get(s.floor_id)
        if got is None or floor is None:
            if s.id in world:
                ck.fail("floorplan", "space_without_mesh", s.id, f"space {s.id} has no mesh")
            continue
        _, pts = got
        poly = np.asarray([p[:2] for p in s.polygon], dtype=np.float64)
        ck.measure("space_xy_m", _set_diff(poly, pts[:, :2]), POS_TOL, "floorplan", s.id, "space")
        z_err = float(np.abs(pts[:, 2] - floor.elevation_m).max())
        ck.measure("space_z_m", z_err, POS_TOL, "floorplan", s.id, "space elevation")
        ck.checked["spaces"] += 1

    # --- openings: node origin and width -------------------------------------------------
    for o in building.openings:
        entry = world.get(o.id)
        floor = floors.get(o.floor_id)
        if entry is None or floor is None:
            continue
        x, y, z, w, _d, _h, _r = decompose_asset_matrix(entry[0])
        xy_err = math.hypot(x - o.position[0], y - o.position[1])
        ck.measure("opening_xy_m", xy_err, POS_TOL, "floorplan", o.id, "opening position")
        ck.measure(
            "opening_z_m", abs(z - floor.elevation_m), POS_TOL, "floorplan", o.id, "opening z"
        )
        ck.measure("opening_width_m", abs(w - o.width_m), POS_TOL, "floorplan", o.id, "width")
        ck.checked["openings"] += 1

    # --- connectors: span the stop elevations ---------------------------------------------
    for v in building.vertical_connectors:
        got = node_geometry(v.id)
        if got is None:
            continue
        _, pts = got
        zs = [floors[f].elevation_m for f in v.floor_ids if f in floors]
        if len(zs) >= 2:
            tol = 0.2  # tube radius + cap
            err = max(abs(pts[:, 2].min() - min(zs)), abs(pts[:, 2].max() - max(zs)))
            if err > tol:
                ck.fail(
                    "floorplan",
                    "connector_span_mismatch",
                    v.id,
                    f"connector spans z {pts[:, 2].min():.2f}..{pts[:, 2].max():.2f}, "
                    f"stops at {min(zs):.2f}..{max(zs):.2f}",
                )
        ck.checked["connectors"] += 1

    # --- assets: position, rotation, footprint, height ------------------------------------
    if expect_assets:
        space_polys = {s.id: Polygon([p[:2] for p in s.polygon]) for s in building.spaces}
        for a in building.assets:
            entry = world.get(a.id)
            floor = floors.get(a.floor_id)
            if entry is None or floor is None:
                continue
            ex = extras_by_name.get(a.id, {})
            m = entry[0]
            x, y, z, w, d, h, rot = decompose_asset_matrix(m)
            fp = a.footprint
            want_w = float(fp.get("width_m", 0.0))
            want_d = float(fp.get("depth_m", 0.0))
            want_r = float(fp.get("rotation_deg", 0.0)) % 360.0
            ck.measure(
                "asset_xy_m",
                math.hypot(x - a.position[0], y - a.position[1]),
                POS_TOL,
                "equipment",
                a.id,
                "asset position",
            )
            ck.measure(
                "asset_z_m", abs(z - floor.elevation_m), POS_TOL, "equipment", a.id, "asset z"
            )
            if want_w > POS_TOL:
                ck.measure(
                    "asset_rotation_deg",
                    angle_diff_deg(rot, want_r),
                    ROT_TOL,
                    "equipment",
                    a.id,
                    "asset rotation",
                )
            scale_err = max(abs(w - want_w), abs(d - want_d))
            ck.measure("asset_scale_m", scale_err, POS_TOL, "equipment", a.id, "asset footprint")
            want_h = float(ex.get("height_m", math.nan))
            ck.measure("asset_height_m", abs(h - want_h), POS_TOL, "equipment", a.id, "height")
            ds = (to_ds @ m @ to_ds.T)[:3, :3]
            cols = ds / np.maximum(np.linalg.norm(ds, axis=0), 1e-12)
            skew = float(np.abs(cols.T @ cols - np.eye(3)).max())
            if skew > 1e-6 or np.linalg.det(ds) <= 0:
                ck.fail("equipment", "skewed_transform", a.id, "asset matrix is not T*Rz*S")
            if ex.get("type") != a.type or ex.get("space_id") != a.space_id:
                ck.fail("equipment", "extras_mismatch", a.id, "asset extras differ from data")
            sp = space_polys.get(a.space_id)
            if sp is not None and not sp.buffer(0.01).contains(Point(a.position[0], a.position[1])):
                ck.warn(f"asset {a.id} centre lies outside its space {a.space_id} (data)")
            ck.checked["assets"] += 1

    if ck.n_warnings > len(ck.warnings):
        ck.warnings.append(f"... {ck.n_warnings - len(ck.warnings)} more warnings")
    return ValidationReport(
        passed=not ck.failures,
        checked=dict(ck.checked),
        max_error={k: float(v) for k, v in ck.max_error.items()},
        failures=ck.failures,
        warnings=ck.warnings,
    )

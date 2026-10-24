"""Preview renders: per-floor plans drawn from the exported GLB, and asset-spec sketches.

Plans are drawn from the GLB's own geometry (meshes projected to dataset XY), so they show
what the model contains, not what the data says. Orientation matches the dataset's
`floor_<level>_plain.png`: x right, y up, equal aspect.
"""

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.collections import LineCollection, PolyCollection  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # type: ignore[import-untyped]  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402

from app.domain.building import BuildingFile  # noqa: E402
from app.pipeline.tools.geo3d.floorplan import (  # noqa: E402
    HALL_LINE_COLOR,
    MARKER_COLOR,
    OPENING_COLORS,
    WALL_COLOR,
    floor_node,
    space_color,
)
from app.pipeline.tools.geo3d.frame import from_gltf_matrix  # noqa: E402
from app.pipeline.tools.geo3d.model import load_glb, read_gltf_json  # noqa: E402
from app.pipeline.tools.geo3d.spec import AssetSpec, spec_to_mesh  # noqa: E402

MAX_LABELS = 400  # space labels per floor
MAX_ASSETS = 20000  # asset footprints per floor
LONG_SIDE_IN = 12.0
DPI = 110


def _rgb(rgba: tuple[int, int, int, int], alpha: float = 1.0) -> tuple[float, ...]:
    return (rgba[0] / 255, rgba[1] / 255, rgba[2] / 255, alpha)


class _Geo:
    """World-space (dataset frame) access to the meshes of a loaded GLB."""

    def __init__(self, glb_path: Path) -> None:
        self.scene = load_glb(glb_path)
        self.to_ds = from_gltf_matrix()

    def triangles(self, name: str) -> tuple[np.ndarray, np.ndarray] | None:
        """(vertices (n,3) dataset frame, faces (m,3)) of a node, or None."""
        if name not in self.scene.graph.nodes:
            return None
        matrix, geom = self.scene.graph.get(name)
        if geom is None or geom not in self.scene.geometry:
            return None
        mesh = self.scene.geometry[geom]
        verts = np.asarray(mesh.vertices, dtype=np.float64)
        hom = np.column_stack([verts, np.ones(len(verts))])
        ds = (self.to_ds @ np.asarray(matrix) @ hom.T).T[:, :3]
        return ds, np.asarray(mesh.faces)

    def footprint(self, name: str) -> np.ndarray | None:
        """XY outline (4, 2) of an instanced unit-box node (local bounds, transformed)."""
        if name not in self.scene.graph.nodes:
            return None
        matrix, geom = self.scene.graph.get(name)
        if geom is None or geom not in self.scene.geometry:
            return None
        b = np.asarray(self.scene.geometry[geom].bounds)  # glTF local frame
        # local glTF (x, ., z) footprint corners at the bottom (y = min)
        x0, x1, y0, z0, z1 = b[0, 0], b[1, 0], b[0, 1], b[0, 2], b[1, 2]
        corners = np.array(
            [[x0, y0, z0, 1], [x1, y0, z0, 1], [x1, y0, z1, 1], [x0, y0, z1, 1]], dtype=float
        )
        ds = (self.to_ds @ np.asarray(matrix) @ corners.T).T
        return ds[:, :2]


def _top_faces(verts: np.ndarray, faces: np.ndarray) -> np.ndarray:
    tri = verts[faces]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    norm = np.linalg.norm(n, axis=1)
    up = n[:, 2] > 0.5 * np.where(norm > 0, norm, 1)
    return tri[up][:, :, :2]


def _boundary_edges(verts: np.ndarray, faces: np.ndarray) -> np.ndarray:
    e = np.vstack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    key = np.sort(e, axis=1)
    _, inv, counts = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    once = counts[inv.reshape(-1)] == 1
    return verts[e[once]][:, :, :2]


def render_floor_plans(
    glb_path: Path, index_path: Path, building: BuildingFile, out_dir: Path
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    gj = read_gltf_json(glb_path)
    geo = _Geo(glb_path)
    nodes = gj.get("nodes", [])
    names = [str(n.get("name", "")) for n in nodes]
    children: dict[str, list[str]] = {}
    extras: dict[str, dict[str, Any]] = {}
    for n in nodes:
        name = str(n.get("name", ""))
        children[name] = [names[c] for c in n.get("children", []) or [] if 0 <= c < len(names)]
        extras[name] = dict(n.get("extras") or {})
    spaces = {s.id: s for s in building.spaces}

    # one extent for all floors so the plans line up
    xy_all: list[np.ndarray] = []
    for name, ex in extras.items():
        if ex.get("kind") == "space":
            got = geo.triangles(name)
            if got is not None:
                xy_all.append(got[0][:, :2])
    if xy_all:
        allxy = np.vstack(xy_all)
        lo, hi = allxy.min(axis=0), allxy.max(axis=0)
    else:
        lo, hi = np.zeros(2), np.ones(2)
    pad = max(1.0, 0.03 * float(max(hi - lo)))
    lo, hi = lo - pad, hi + pad
    span = np.maximum(hi - lo, 1e-3)
    scale = LONG_SIDE_IN / float(span.max())
    figsize = (max(3.0, float(span[0]) * scale), max(3.0, float(span[1]) * scale + 0.5))

    paths: list[Path] = []
    for floor in sorted(building.floors, key=lambda f: f.level):
        kids = children.get(floor_node(floor.id), [])
        fills: list[np.ndarray] = []
        fill_colors: list[tuple[float, ...]] = []
        outlines: list[np.ndarray] = []
        walls: list[np.ndarray] = []
        lines: list[np.ndarray] = []
        markers: list[np.ndarray] = []
        openings: list[np.ndarray] = []
        opening_colors: list[tuple[float, ...]] = []
        asset_fp: list[np.ndarray] = []
        labels: list[tuple[float, float, str]] = []
        n_assets = sum(1 for k in kids if extras.get(k, {}).get("kind") == "asset")
        for k in kids:
            ex = extras.get(k, {})
            kind = ex.get("kind")
            if kind == "space":
                got = geo.triangles(k)
                if got is None:
                    continue
                v, f = got
                tri = v[f][:, :, :2]
                fills.append(tri)
                col = _rgb(space_color(str(ex.get("type", "")), str(ex.get("room_type", ""))))
                fill_colors.extend([col] * len(tri))
                outlines.append(_boundary_edges(v, f))
                sp = spaces.get(k)
                if sp is not None and sp.ref:
                    rp = Polygon([p[:2] for p in sp.polygon]).representative_point()
                    labels.append((rp.x, rp.y, sp.ref))
            elif kind in ("walls", "hall_lines", "marker"):
                got = geo.triangles(k)
                if got is None:
                    continue
                top = _top_faces(*got)
                {"walls": walls, "hall_lines": lines, "marker": markers}[kind].append(top)
            elif kind == "opening":
                fp = geo.footprint(k)
                if fp is not None:
                    openings.append(fp)
                    kind_col = OPENING_COLORS.get(str(ex.get("opening_kind")), (120, 120, 120, 255))
                    opening_colors.append(_rgb(kind_col))
            elif kind == "asset" and n_assets <= MAX_ASSETS:
                fp = geo.footprint(k)
                if fp is not None:
                    asset_fp.append(fp)

        fig, ax = plt.subplots(figsize=figsize, dpi=DPI)
        ax.set_xlim(lo[0], hi[0])
        ax.set_ylim(lo[1], hi[1])
        ax.set_aspect("equal")
        ax.axis("off")
        title = f"{building.building.name} — {floor.name} (level {floor.level})"
        ax.set_title(title, fontsize=10)
        if fills:
            ax.add_collection(
                PolyCollection(
                    list(np.vstack(fills)),
                    facecolors=fill_colors,
                    edgecolors=fill_colors,
                    linewidths=0.3,
                    zorder=1,
                )
            )
        if outlines:
            ax.add_collection(
                LineCollection(
                    list(np.vstack(outlines)), colors="#888888", linewidths=0.3, zorder=2
                )
            )
        for group, rgba, z in (
            (lines, HALL_LINE_COLOR, 3),
            (markers, MARKER_COLOR, 4),
            (walls, WALL_COLOR, 5),
        ):
            if group:
                tris = np.vstack(group)
                if len(tris):
                    c = _rgb(rgba, 0.6 if group is markers else 1.0)
                    ax.add_collection(
                        PolyCollection(
                            list(tris), facecolors=c, edgecolors=c, linewidths=0.2, zorder=z
                        )
                    )
        if asset_fp:
            ax.add_collection(
                PolyCollection(
                    asset_fp,
                    facecolors=(0.25, 0.4, 0.75, 0.15),
                    edgecolors=(0.15, 0.25, 0.6, 0.9),
                    linewidths=0.4,
                    zorder=6,
                )
            )
        if openings:
            ax.add_collection(
                PolyCollection(openings, facecolors=opening_colors, edgecolors="none", zorder=7)
            )
        if not kids or not fills:
            ax.text(
                (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, "(empty floor)", ha="center", va="center"
            )
        if len(labels) <= MAX_LABELS:
            size = 5 if len(labels) > 120 else 6
            for x, y, text in labels:
                ax.text(x, y, text, fontsize=size, ha="center", va="center", zorder=8)
        out = out_dir / f"plan_floor_{floor.level}.png"
        fig.savefig(out, dpi=DPI, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        paths.append(out)
    return paths


def render_spec(spec: AssetSpec, out_png: Path) -> Path:
    """Iso, front and top views of a spec's unit-box mesh (dataset frame, z up)."""
    mesh = spec_to_mesh(spec)
    v = np.asarray(mesh.vertices)
    f = np.asarray(mesh.faces)
    tri = v[f]
    normals = np.asarray(mesh.face_normals)
    light = np.array([0.4, -0.6, 0.7])
    light /= np.linalg.norm(light)
    shade = 0.45 + 0.55 * np.clip(normals @ light, 0, 1)
    base = np.array([0.35, 0.55, 0.85])
    colors = [(*(base * s), 1.0) for s in shade]

    fig = plt.figure(figsize=(10, 3.6), dpi=100)
    ax = fig.add_subplot(1, 3, 1, projection="3d")
    ax.add_collection3d(Poly3DCollection(list(tri), facecolors=colors, edgecolors=(0, 0, 0, 0.15)))
    # unit box wireframe
    cx = [-0.5, 0.5]
    for x in cx:
        for y in cx:
            ax.plot([x, x], [y, y], [0, 1], color="0.7", lw=0.5)
    for z in (0, 1):
        ax.plot([-0.5, 0.5, 0.5, -0.5, -0.5], [-0.5, -0.5, 0.5, 0.5, -0.5], [z] * 5, "0.7", lw=0.5)
    ax.set_xlim(-0.5, 0.5)
    ax.set_ylim(-0.5, 0.5)
    ax.set_zlim(0, 1)
    ax.set_box_aspect((1, 1, 1))
    ax.view_init(elev=25, azim=-60)
    ax.set_xlabel("x (width)", fontsize=7)
    ax.set_ylabel("y (depth)", fontsize=7)
    ax.set_zlabel("z", fontsize=7)
    ax.tick_params(labelsize=6)
    ax.set_title("iso", fontsize=9)

    order_views = (
        ("front (x-z, looking +y)", 0, 2, 1, True),
        ("top (x-y)", 0, 1, 2, False),
    )
    for i, (title, a, b, depth_axis, nearest_small) in enumerate(order_views):
        ax2 = fig.add_subplot(1, 3, i + 2)
        depth = tri[:, :, depth_axis].mean(axis=1)
        order = np.argsort(-depth if nearest_small else depth)  # painter's order
        polys = [tri[j][:, [a, b]] for j in order]
        ax2.add_collection(
            PolyCollection(
                polys,
                facecolors=[colors[j] for j in order],
                edgecolors=(0, 0, 0, 0.2),
                linewidths=0.3,
            )
        )
        if b == 2:
            ax2.plot([-0.5, 0.5, 0.5, -0.5, -0.5], [0, 0, 1, 1, 0], "0.6", lw=0.6)
            ax2.set_ylim(-0.05, 1.05)
        else:
            ax2.plot([-0.5, 0.5, 0.5, -0.5, -0.5], [-0.5, -0.5, 0.5, 0.5, -0.5], "0.6", lw=0.6)
            ax2.set_ylim(-0.55, 0.55)
        ax2.set_xlim(-0.55, 0.55)
        ax2.set_aspect("equal")
        ax2.set_title(title, fontsize=9)
        ax2.tick_params(labelsize=6)
    fig.suptitle(
        f"{spec.type} ({spec.category}), default height {spec.default_height_m:g} m", fontsize=10
    )
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_png

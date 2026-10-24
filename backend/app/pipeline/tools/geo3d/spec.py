"""Declarative asset specs: the asset library's parametric generators.

A spec describes one asset `type` as primitives inside the *unit box*, in the dataset
frame (z up): x in [-0.5, 0.5] runs along the footprint width, y in [-0.5, 0.5] along the
depth, z in [0, 1] is height, and the origin is the bottom centre. Placing an asset scales
the unit box to width_m x depth_m x height, so every instance stays to scale. Specs are
data, never code, so specs written by an agent are safe to render.
"""

from datetime import datetime
from typing import Annotated, Literal

import numpy as np
import trimesh
from pydantic import BaseModel, Field

from app.pipeline.state import utcnow

Vec3 = Annotated[list[float], Field(min_length=3, max_length=3)]
Vec2 = Annotated[list[float], Field(min_length=2, max_length=2)]

EPS = 1e-6
MAX_PRIMITIVES = 48
CYLINDER_SECTIONS = 16


class Box(BaseModel):
    kind: Literal["box"] = "box"
    center: Vec3
    size: Vec3 = Field(description="Full extents along x, y, z (unit-box fractions).")


class Cylinder(BaseModel):
    kind: Literal["cylinder"] = "cylinder"
    center: Vec3
    radius: float = Field(gt=0)
    length: float = Field(gt=0, description="Extent along `axis`.")
    axis: Literal["x", "y", "z"] = "z"


class Prism(BaseModel):
    """A polygon in the xy plane extruded from z0 to z1."""

    kind: Literal["prism"] = "prism"
    polygon: list[Vec2] = Field(min_length=3)
    z0: float
    z1: float


Primitive = Annotated[Box | Cylinder | Prism, Field(discriminator="kind")]


class AssetSpec(BaseModel):
    type: str
    category: str
    description: str = ""
    default_height_m: float = Field(gt=0, le=12)
    primitives: list[Primitive] = Field(min_length=1, max_length=MAX_PRIMITIVES)


class LibraryEntry(BaseModel):
    spec: AssetSpec
    provenance: Literal["seed", "agent", "fallback"]
    run_id: str | None = None
    version: int = 1
    created_at: datetime = Field(default_factory=utcnow)


def _bounds(p: Box | Cylinder | Prism) -> tuple[np.ndarray, np.ndarray]:
    if isinstance(p, Box):
        c, half = np.asarray(p.center), np.asarray(p.size) / 2
        return c - half, c + half
    if isinstance(p, Cylinder):
        c = np.asarray(p.center)
        half = np.full(3, p.radius)
        half["xyz".index(p.axis)] = p.length / 2
        return c - half, c + half
    xy = np.asarray(p.polygon)
    return (
        np.array([*xy.min(axis=0), min(p.z0, p.z1)]),
        np.array([*xy.max(axis=0), max(p.z0, p.z1)]),
    )


def validate_spec(spec: AssetSpec) -> list[str]:
    """Problems that make a spec unusable; empty means valid."""
    problems: list[str] = []
    lo_all, hi_all = np.full(3, np.inf), np.full(3, -np.inf)
    for i, p in enumerate(spec.primitives):
        if isinstance(p, Box) and min(p.size) <= 0:
            problems.append(f"primitive {i}: box sizes must be > 0")
            continue
        if isinstance(p, Prism):
            if abs(p.z1 - p.z0) <= EPS:
                problems.append(f"primitive {i}: prism needs z1 != z0")
                continue
            from shapely.geometry import Polygon

            poly = Polygon(p.polygon)
            if not poly.is_valid or poly.area <= EPS:
                problems.append(f"primitive {i}: prism polygon is invalid or empty")
                continue
        lo, hi = _bounds(p)
        if (lo < np.array([-0.5, -0.5, 0.0]) - EPS).any() or (
            hi > np.array([0.5, 0.5, 1.0]) + EPS
        ).any():
            problems.append(
                f"primitive {i} ({p.kind}) leaves the unit box: "
                f"min {np.round(lo, 3).tolist()} max {np.round(hi, 3).tolist()}"
            )
        lo_all, hi_all = np.minimum(lo_all, lo), np.maximum(hi_all, hi)
    if not problems:
        # The model should fill its footprint and height, or it renders under-sized.
        span = hi_all - lo_all
        for axis, name in enumerate("xyz"):
            if span[axis] < 0.6:
                problems.append(f"spec only spans {span[axis]:.2f} of the unit box along {name}")
        if lo_all[2] > 0.05:
            problems.append(f"spec floats {lo_all[2]:.2f} above the floor (min z must be ~0)")
    return problems


def _primitive_mesh(p: Box | Cylinder | Prism) -> trimesh.Trimesh:
    if isinstance(p, Box):
        return trimesh.creation.box(
            extents=p.size, transform=trimesh.transformations.translation_matrix(p.center)
        )
    if isinstance(p, Cylinder):
        mesh = trimesh.creation.cylinder(
            radius=p.radius, height=p.length, sections=CYLINDER_SECTIONS
        )
        if p.axis == "x":
            mesh.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
        elif p.axis == "y":
            mesh.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
        mesh.apply_translation(p.center)
        return mesh
    from shapely.geometry import Polygon

    z0, z1 = sorted((p.z0, p.z1))
    mesh = trimesh.creation.extrude_polygon(Polygon(p.polygon), height=z1 - z0)
    mesh.apply_translation([0, 0, z0])
    return mesh


def spec_to_mesh(spec: AssetSpec) -> trimesh.Trimesh:
    """Unit-box mesh of a spec in the dataset frame (z up, origin bottom centre)."""
    mesh = trimesh.util.concatenate([_primitive_mesh(p) for p in spec.primitives])
    assert isinstance(mesh, trimesh.Trimesh)
    return mesh


def fallback_spec(asset_type: str, category: str, height_m: float) -> AssetSpec:
    """Plain unit box, used when no library entry exists (offline or agent failure)."""
    return AssetSpec(
        type=asset_type,
        category=category,
        description="Placeholder box (no library entry yet).",
        default_height_m=height_m,
        primitives=[Box(center=[0, 0, 0.5], size=[1, 1, 1])],
    )


# Heights used when a type has no library entry, by category (metres).
CATEGORY_HEIGHT: dict[str, float] = {
    "furniture": 0.9,
    "equipment": 1.2,
    "fixture": 0.9,
    "machine": 2.0,
}
DEFAULT_HEIGHT = 1.0

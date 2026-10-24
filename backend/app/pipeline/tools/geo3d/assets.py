"""Asset placement: one shared unit-box mesh per asset type, one node per asset.

Each node's matrix scales the type's unit-box mesh to width x depth x height, rotates it
`rotation_deg` counter-clockwise about z (width along the rotation direction) and puts its
origin (footprint centre on the floor surface) at (x, y, floor elevation). Height comes
from the spec (`default_height_m`); types the library cannot resolve fall back to a plain
box with the category height.
"""

from collections import Counter
from collections.abc import Callable
from typing import Any

import trimesh
from pydantic import BaseModel, Field

from app.domain.building import AssetIn, BuildingFile
from app.pipeline.tools.geo3d.floorplan import FloorplanResult, floor_node, material
from app.pipeline.tools.geo3d.frame import asset_matrix, to_gltf_matrix
from app.pipeline.tools.geo3d.spec import (
    CATEGORY_HEIGHT,
    DEFAULT_HEIGHT,
    AssetSpec,
    fallback_spec,
    spec_to_mesh,
)

CATEGORY_COLORS: dict[str, tuple[int, int, int, int]] = {
    "furniture": (120, 150, 200, 255),
    "equipment": (90, 170, 120, 255),
    "fixture": (170, 170, 180, 255),
    "machine": (220, 120, 50, 255),
}

Resolver = Callable[[str, str], AssetSpec | None]


class AssetPlacement(BaseModel):
    placed: int = 0
    missing_types: list[str] = Field(default_factory=list)
    by_type: dict[str, int] = Field(default_factory=dict)
    skipped: list[str] = Field(default_factory=list, description="Asset ids not placed.")


def asset_type_geometry(asset_type: str) -> str:
    return f"asset_type:{asset_type}"


def spec_mesh_gltf(spec: AssetSpec, category: str) -> trimesh.Trimesh:
    mesh = spec_to_mesh(spec)
    mesh.apply_transform(to_gltf_matrix())
    mesh.visual = trimesh.visual.TextureVisuals(
        material=material(CATEGORY_COLORS.get(category, (150, 150, 150, 255)))
    )
    return mesh


def _footprint(a: AssetIn) -> tuple[float, float, float]:
    fp: dict[str, Any] = a.footprint
    return (
        float(fp.get("width_m", 0.0)),
        float(fp.get("depth_m", 0.0)),
        float(fp.get("rotation_deg", 0.0)),
    )


def place_assets(
    result: FloorplanResult, building: BuildingFile, resolve: Resolver
) -> AssetPlacement:
    scene = result.scene
    floors = {f.id: f for f in building.floors}
    specs: dict[str, tuple[AssetSpec, str]] = {}
    missing: list[str] = []
    by_type: Counter[str] = Counter()
    skipped: list[str] = []
    for a in building.assets:
        floor = floors.get(a.floor_id)
        width, depth, rot = _footprint(a)
        if floor is None or floor_node(a.floor_id) not in result.index:
            skipped.append(a.id)
            continue
        if a.type not in specs:
            spec = resolve(a.type, a.category)
            provenance = "library"
            if spec is None:
                spec = fallback_spec(
                    a.type, a.category, CATEGORY_HEIGHT.get(a.category, DEFAULT_HEIGHT)
                )
                provenance = "fallback"
                missing.append(a.type)
            specs[a.type] = (spec, provenance)
            scene.geometry[asset_type_geometry(a.type)] = spec_mesh_gltf(spec, a.category)
        spec, provenance = specs[a.type]
        height = spec.default_height_m
        extras: dict[str, Any] = {
            "kind": "asset",
            "id": a.id,
            "type": a.type,
            "category": a.category,
            "space_id": a.space_id,
            "floor_id": a.floor_id,
            "height_m": height,
            "spec_provenance": provenance,
        }
        scene.graph.update(
            frame_to=a.id,
            frame_from=floor_node(a.floor_id),
            geometry=asset_type_geometry(a.type),
            matrix=asset_matrix(
                float(a.position[0]),
                float(a.position[1]),
                floor.elevation_m,
                width,
                depth,
                height,
                rot,
            ),
            metadata=extras,
        )
        result.index[a.id] = extras
        by_type[a.type] += 1
    placed = sum(by_type.values())
    result.stats["assets"] = placed
    return AssetPlacement(
        placed=placed, missing_types=sorted(missing), by_type=dict(by_type), skipped=skipped
    )

"""Tiny builders for synthetic building files used across pipeline tests.

Defaults: one floor ``F0``, two adjacent 10x10 m rooms ``S1``/``S2`` joined by door ``O1`` at
(10, 5) and one asset ``A1`` in ``S1``. Pass lists to replace a collection entirely.
"""

from typing import Any

from app.domain.building import BuildingFile


def floor(
    id: str = "F0", level: int = 0, elevation_m: float = 0.0, height_m: float = 3.0
) -> dict[str, Any]:
    return {
        "id": id,
        "level": level,
        "name": f"Level {level}",
        "elevation_m": elevation_m,
        "height_m": height_m,
    }


def rect(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def space(
    id: str,
    floor_id: str = "F0",
    box: tuple[float, float, float, float] = (0, 0, 10, 10),
    type: str = "room",
    ref: str | None = None,
    name: str | None = None,
    polygon: list[list[float]] | None = None,
) -> dict[str, Any]:
    ref = id if ref is None else ref
    return {
        "id": id,
        "floor_id": floor_id,
        "type": type,
        "room_type": "office",
        "name": f"Room {ref}" if name is None else name,
        "ref": ref,
        "polygon": rect(*box) if polygon is None else polygon,
        "synthetic": False,
    }


def opening(
    id: str,
    space_ids: list[str],
    position: tuple[float, float] = (10.0, 5.0),
    floor_id: str = "F0",
    width_m: float = 0.9,
    kind: str = "door",
) -> dict[str, Any]:
    return {
        "id": id,
        "floor_id": floor_id,
        "space_ids": space_ids,
        "kind": kind,
        "position": list(position),
        "width_m": width_m,
        "step_free": True,
        "synthetic": False,
    }


def connector(
    id: str, floor_ids: list[str], space_ids: list[str], kind: str = "stairs"
) -> dict[str, Any]:
    return {
        "id": id,
        "kind": kind,
        "name": id,
        "floor_ids": floor_ids,
        "space_ids": space_ids,
        "step_free": kind == "lift",
        "synthetic": False,
    }


def asset(
    id: str,
    space_id: str = "S1",
    position: tuple[float, float] = (5.0, 5.0),
    floor_id: str = "F0",
    width_m: float = 1.0,
    depth_m: float = 1.0,
    rotation_deg: float = 0.0,
) -> dict[str, Any]:
    return {
        "id": id,
        "name": f"Asset {id}",
        "category": "machine",
        "type": "cnc_mill",
        "space_id": space_id,
        "floor_id": floor_id,
        "position": list(position),
        "footprint": {"width_m": width_m, "depth_m": depth_m, "rotation_deg": rotation_deg},
        "source": "generated",
    }


def make_building_dict(
    floors: list[dict[str, Any]] | None = None,
    spaces: list[dict[str, Any]] | None = None,
    openings: list[dict[str, Any]] | None = None,
    vertical_connectors: list[dict[str, Any]] | None = None,
    assets: list[dict[str, Any]] | None = None,
    building_id: str = "test-001",
) -> dict[str, Any]:
    return {
        "building": {
            "id": building_id,
            "name": "Test building",
            "source": "synthetic",
            "source_ref": {},
            "license": "n/a",
            "attribution": "n/a",
            "split": "dev",
            "units": "m",
        },
        "floors": [floor()] if floors is None else floors,
        "spaces": ([space("S1"), space("S2", box=(10, 0, 20, 10))] if spaces is None else spaces),
        "openings": [opening("O1", ["S1", "S2"])] if openings is None else openings,
        "vertical_connectors": [] if vertical_connectors is None else vertical_connectors,
        "assets": [asset("A1")] if assets is None else assets,
    }


def make_building(**kwargs: Any) -> BuildingFile:
    return BuildingFile.model_validate(make_building_dict(**kwargs))

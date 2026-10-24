"""Building model in the starter-kit format (building.json); validated on ingestion."""

from typing import Any

from pydantic import BaseModel, ConfigDict

Point = list[float]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BuildingMeta(_Strict):
    id: str
    name: str
    source: str
    source_ref: dict[str, Any]
    license: str
    attribution: str
    split: str
    units: str


class FloorIn(_Strict):
    id: str
    level: int
    name: str
    elevation_m: float
    height_m: float


class SpaceIn(_Strict):
    id: str
    floor_id: str
    type: str
    room_type: str
    name: str
    ref: str
    polygon: list[Point]
    synthetic: bool


class OpeningIn(_Strict):
    id: str
    floor_id: str
    space_ids: list[str]
    kind: str
    position: Point
    width_m: float
    step_free: bool
    synthetic: bool


class VerticalConnectorIn(_Strict):
    id: str
    kind: str
    name: str
    floor_ids: list[str]
    space_ids: list[str]
    step_free: bool
    synthetic: bool


class AssetIn(_Strict):
    id: str
    name: str
    category: str
    type: str
    space_id: str
    floor_id: str
    position: Point
    footprint: dict[str, Any]
    source: str


class BuildingFile(_Strict):
    building: BuildingMeta
    floors: list[FloorIn]
    spaces: list[SpaceIn]
    openings: list[OpeningIn]
    vertical_connectors: list[VerticalConnectorIn]
    assets: list[AssetIn]

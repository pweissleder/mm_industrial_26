"""Read a starter-kit floor plan SVG (Matplotlib export) back into plan primitives.

The kit draws every floor the same way, inside `figure_1/axes_1`, in data order:
- `patch_1` is the figure background;
- one `patch_N` per space (stroke `#333333`, fill colour = space type);
- one `patch_N` per asset (opacity 0.75, fill colour = asset category), in equipment.csv order;
- `text_N` labels (the space `ref`), whose string Matplotlib keeps as an XML comment; the
  last text is the title, "<building name> — <floor name>";
- `line2d_N` markers at each opening (colour = kind).
There are no axes, so pixel→metre comes from fitting the asset patches to equipment.csv
(`fit_transform`): `u = k·x + bx`, `v = -k·y + by` (y flipped, uniform scale, no rotation).
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon

SPACE_STROKE = "#333333"
SPACE_TYPES = {
    "#f3efe6": "room",
    "#fff2b3": "hall",
    "#dcdcdc": "corridor",
    "#f4b183": "stair",
    "#b4a7d6": "lift",
    "#cfe8c4": "outdoor",
}
ASSET_CATEGORIES = {
    "#7f6a4f": "furniture",
    "#2e7d32": "equipment",
    "#4f81bd": "fixture",
    "#c0504d": "machine",
}
OPENING_KINDS = {"#8b4513": "door", "#9e9e9e": "opening", "#d32f2f": "entrance"}
TITLE_SEPARATOR = " — "

_NUMBER = re.compile(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?")
_TRANSLATE = re.compile(r"translate\(\s*([-+\d.eE]+)[\s,]+([-+\d.eE]+)\s*\)")

PointPx = tuple[float, float]


@dataclass(frozen=True)
class Transform:
    """Pixel ↔ metre mapping of one floor: `u = k·x + bx`, `v = -k·y + by`."""

    k: float
    bx: float
    by: float

    def to_m(self, u: float, v: float) -> tuple[float, float]:
        return ((u - self.bx) / self.k, (self.by - v) / self.k)


@dataclass
class PlanSpace:
    polygon_px: list[PointPx]
    type: str
    room_type: str | None = None  # known only from vision tracing or the CSV
    name: str | None = None


@dataclass
class PlanOpening:
    position_px: PointPx
    kind: str


@dataclass
class PlanFloor:
    """One floor as drawn, in pixels; the same shape for SVG parsing and vision tracing."""

    level: int
    source: str  # "svg" | "vision"
    spaces: list[PlanSpace] = field(default_factory=list)
    openings: list[PlanOpening] = field(default_factory=list)
    asset_centres_px: list[PointPx] = field(default_factory=list)
    labels: list[tuple[str, PointPx]] = field(default_factory=list)
    building_name: str | None = None
    floor_name: str | None = None
    transform: Transform | None = None  # vision tracing knows its scale; SVGs are fitted


def path_points(d: str) -> list[PointPx]:
    """Vertices of the first sub-path of a straight-line SVG path (`M … L … z`)."""
    if re.search("[CQAST]", d, re.I):
        raise ValueError("nonpolygonal patch")
    first = re.split(r"[zZ]", d)[0]
    values = [float(v) for v in _NUMBER.findall(first)]
    points = list(zip(values[::2], values[1::2], strict=False))
    if len(points) > 1 and points[0] == points[-1]:
        points.pop()
    return points


def _style(element: ET.Element) -> dict[str, str]:
    pairs = (p.split(":", 1) for p in element.attrib.get("style", "").split(";") if ":" in p)
    return {k.strip(): v.strip().lower() for k, v in pairs}


def _comment(group: ET.Element) -> str | None:
    for child in group:
        if child.tag is ET.Comment and child.text:  # type: ignore[comparison-overlap]
            return child.text.strip()
    return None


def parse_floor_svg(path: Path, level: int) -> PlanFloor:
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    root = ET.fromstring(path.read_bytes(), parser=parser)
    floor = PlanFloor(level=level, source="svg")
    texts: list[tuple[str, PointPx]] = []
    for group in root.iter():
        gid = group.attrib.get("id", "")
        if gid.startswith("patch_") and gid != "patch_1":
            shape = next((e for e in group if e.tag.endswith("path")), None)
            if shape is None:
                continue
            style = _style(shape)
            fill = style.get("fill", "")
            if style.get("stroke") == SPACE_STROKE and fill in SPACE_TYPES:
                floor.spaces.append(PlanSpace(path_points(shape.attrib["d"]), SPACE_TYPES[fill]))
            elif fill in ASSET_CATEGORIES:
                centre = Polygon(path_points(shape.attrib["d"])).centroid
                floor.asset_centres_px.append((centre.x, centre.y))
        elif gid.startswith("text_"):
            text = _comment(group)
            placed = next((e for e in group if "transform" in e.attrib), None)
            match = _TRANSLATE.search(placed.attrib["transform"]) if placed is not None else None
            if text and match:
                texts.append((text, (float(match[1]), float(match[2]))))
        elif gid.startswith("line2d_"):
            colour = _style(next((e for e in group if e.tag.endswith("path")), group)).get(
                "stroke", ""
            )
            kind = OPENING_KINDS.get(colour)
            if kind is None:
                continue
            for use in group.iter():
                if use.tag.endswith("use") and "x" in use.attrib:
                    floor.openings.append(
                        PlanOpening((float(use.attrib["x"]), float(use.attrib["y"])), kind)
                    )
    if texts and TITLE_SEPARATOR in texts[-1][0]:
        title, _ = texts.pop()
        floor.building_name, _, floor.floor_name = title.rpartition(TITLE_SEPARATOR)
    floor.labels = texts
    return floor


def fit_transform(
    centres_px: list[PointPx], positions_m: list[tuple[float, float]]
) -> tuple[Transform, float] | None:
    """Least-squares `k, bx, by` from asset centres (same order); also the max residual (px)."""
    if len(centres_px) != len(positions_m) or len(centres_px) < 2:
        return None
    rows, rhs = [], []
    for (u, v), (x, y) in zip(centres_px, positions_m, strict=True):
        rows += [[x, 1.0, 0.0], [-y, 0.0, 1.0]]
        rhs += [u, v]
    a, b = np.array(rows), np.array(rhs)
    (k, bx, by), *_ = np.linalg.lstsq(a, b, rcond=None)
    if k <= 0:
        return None
    residual = float(np.abs(a @ np.array([k, bx, by]) - b).max())
    return Transform(float(k), float(bx), float(by)), residual

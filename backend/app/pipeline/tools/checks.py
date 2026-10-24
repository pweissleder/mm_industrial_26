"""Deterministic raw-data checks for building files.

``run_checks`` is the only entry point the validation agent needs; ``ISSUE_CLASSES`` documents
each class (severity, meaning, how to fix it) and is rendered into the agent prompt.
Issue ids are ``<class>:<sorted entity ids joined by '|'>`` so they stay stable across patches.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from statistics import median
from typing import Any

from shapely.geometry import Point, Polygon
from shapely.strtree import STRtree

from app.domain.building import BuildingFile, SpaceIn
from app.pipeline.state import Issue, Severity

MIN_POLYGON_AREA_M2 = 0.5
OPENING_BOUNDARY_TOL_M = 0.5
OVERLAP_FRACTION = 0.05
OVERLAP_MIN_AREA_M2 = 0.5
STOP_DISTANCE_TOL_M = 5.0
MIN_OPENING_WIDTH_M = 0.5
MISALIGNMENT_TOL_M = 2.0
MISALIGNMENT_SPREAD_M = 1.0
ASSET_OUTSIDE_FRACTION = 0.25
ASSET_COLLISION_FRACTION = 0.30


@dataclass(frozen=True)
class IssueClassInfo:
    severity: Severity
    description: str
    fix_hint: str


ISSUE_CLASSES: dict[str, IssueClassInfo] = {
    "duplicate_id": IssueClassInfo(
        Severity.ERROR,
        "Two entities (floor/space/opening/connector/asset) share the same id.",
        "Rename the later entity with a unique id and update every reference to it "
        "(asset.space_id, opening.space_ids, connector.space_ids). Ids must stay unique.",
    ),
    "dangling_reference": IssueClassInfo(
        Severity.ERROR,
        "A reference points to a missing floor/space, an asset's floor differs from its "
        "space's floor, or a connector's floor_ids/space_ids differ in length.",
        "Repoint the reference (space_id, floor_id, space_ids, floor_ids) to the right existing "
        "entity, or remove the referencing entity if it is meaningless. For connectors keep "
        "floor_ids[i] equal to the floor of space_ids[i].",
    ),
    "invalid_polygon": IssueClassInfo(
        Severity.ERROR,
        "Space polygon has <3 vertices, is self-intersecting/invalid or has area < 0.5 m2.",
        "Replace /spaces/<i>/polygon with a valid simple ring (or remove the space together "
        "with the entities that reference it).",
    ),
    "rotation_out_of_range": IssueClassInfo(
        Severity.WARNING,
        "Asset footprint.rotation_deg is outside [0, 360).",
        "Replace /assets/<i>/footprint/rotation_deg with the value modulo 360 "
        "(details.suggested_deg). Automatic fix available.",
    ),
    "name_ref_mismatch": IssueClassInfo(
        Severity.INFO,
        "Space name does not contain its ref code.",
        "Usually harmless; optionally align /spaces/<i>/name or /spaces/<i>/ref. Accepting is OK.",
    ),
    "opening_off_boundary": IssueClassInfo(
        Severity.WARNING,
        "Opening position is more than 0.5 m away from the boundary of a space it joins.",
        "Move /openings/<i>/position onto the shared wall (details.nearest_boundary_point), or "
        "fix opening.space_ids if it joins the wrong spaces. Accept if floor is globally "
        "shifted (see floor_misalignment).",
    ),
    "overlapping_spaces": IssueClassInfo(
        Severity.WARNING,
        "Two spaces on the same floor overlap by >5% of the smaller one and >0.5 m2.",
        "Only fix when one polygon is clearly wrong (edit its vertices). Overlap of a stair/lift "
        "shaft with a corridor or a nested hall is often intentional; otherwise accept.",
    ),
    "duplicate_space_name": IssueClassInfo(
        Severity.INFO,
        "Several spaces share the same name within the building.",
        "Optional: disambiguate /spaces/<i>/name. Accepting is fine.",
    ),
    "duplicate_space_ref": IssueClassInfo(
        Severity.WARNING,
        "Several spaces share the same ref within the building.",
        "Make /spaces/<i>/ref unique (e.g. append floor level or a letter) unless the spaces "
        "are intentionally one logical room split in parts.",
    ),
    "lift_stop_not_lift_space": IssueClassInfo(
        Severity.WARNING,
        "A lift connector stops at a space whose type is not 'lift'.",
        "Point connector.space_ids[i] at the lift-type space under it, or set the space type to "
        "'lift' via /spaces/<i>/type if the space is the shaft. Accept if no shaft is modeled.",
    ),
    "connector_stops_far_apart": IssueClassInfo(
        Severity.WARNING,
        "Consecutive stops of a connector are >5 m apart in XY.",
        "Usually caused by floor_misalignment (translate the floor, see its fix_hint) or a wrong "
        "stop space: fix /vertical_connectors/<i>/space_ids.",
    ),
    "connector_skips_floor": IssueClassInfo(
        Severity.WARNING,
        "A connector jumps over an existing floor level between two consecutive stops.",
        "Add the skipped floor's stop (insert into floor_ids and space_ids at the same index) "
        "or accept if the connector really bypasses that floor.",
    ),
    "unused_vertical_space": IssueClassInfo(
        Severity.WARNING,
        "A stair/lift-type space is used by no vertical connector.",
        "Add the space to a matching connector (floor_ids and space_ids at the same index), "
        "create a connector, or change /spaces/<i>/type if it is not vertical circulation.",
    ),
    "overlapping_storeys": IssueClassInfo(
        Severity.INFO,
        "A floor's elevation_m is below previous floor elevation + height_m.",
        "Informational (typical for generated data). Accept unless elevation_m is clearly wrong; "
        "then patch /floors/<i>/elevation_m (details.expected_elevation_m).",
    ),
    "level_gap": IssueClassInfo(
        Severity.INFO,
        "Floor levels are not consecutive integers.",
        "Informational; accept unless a floor is missing from the file.",
    ),
    "narrow_opening": IssueClassInfo(
        Severity.WARNING,
        "Opening width_m is below 0.5 m.",
        "Set /openings/<i>/width_m to a plausible width (door 0.9, details.suggested_width_m) "
        "or remove the opening if it is a data artifact.",
    ),
    "floor_misalignment": IssueClassInfo(
        Severity.WARNING,
        "A floor is XY-shifted by >2 m relative to the reference floor, estimated from the "
        "stops of the same connector.",
        "Apply the ops from translate_floor_ops(doc, floor_id, details.suggested_dx, "
        "details.suggested_dy) as one patch: it shifts all spaces, openings and assets of that "
        "floor. Re-run checks; stop distances should drop.",
    ),
    "duplicate_entrance": IssueClassInfo(
        Severity.WARNING,
        "Two openings join the same spaces at the same position.",
        "Remove the later opening (remove /openings/<i>). Automatic fix available.",
    ),
    "asset_outside_space": IssueClassInfo(
        Severity.WARNING,
        "More than 25% of the asset footprint lies outside its space polygon.",
        "Move /assets/<i>/position to details.suggested_position (inside the space), or fix "
        "asset.space_id if it belongs to another space.",
    ),
    "colliding_assets": IssueClassInfo(
        Severity.INFO,
        "Two assets in the same space overlap by more than 30% of the smaller footprint.",
        "Optional: move one asset apart. Accepting is fine for dense generated layouts.",
    ),
}


def _normalised_deg(rot: float) -> float:
    """rot mod 360, rounded, never 360.0 after rounding (e.g. -0.0001 -> 0.0)."""
    value = _r(rot % 360.0)
    return 0.0 if value >= 360.0 else value


def _issue(cls: str, entity_ids: list[str], message: str, **details: Any) -> Issue:
    ids = sorted(set(entity_ids))
    return Issue(
        id=f"{cls}:{'|'.join(ids)}",
        issue_class=cls,
        severity=ISSUE_CLASSES[cls].severity,
        entity_ids=ids,
        message=message,
        details=details,
    )


def _r(x: float, n: int = 3) -> float:
    return round(float(x), n)


# --- geometry helpers ---------------------------------------------------------------


def space_polygon(space: SpaceIn) -> Polygon | None:
    """Valid shapely polygon of a space, or None when the ring is unusable."""
    if len(space.polygon) < 3:
        return None
    try:
        poly = Polygon([(p[0], p[1]) for p in space.polygon])
    except (ValueError, IndexError):
        return None
    if poly.is_empty or not poly.is_valid:
        return None
    return poly


def footprint_polygon(
    position: list[float], width_m: float, depth_m: float, rotation_deg: float
) -> Polygon:
    """Rectangle centred at position; width along rotation_deg (CCW from +x), depth across."""
    a = math.radians(rotation_deg)
    ux, uy = math.cos(a), math.sin(a)
    vx, vy = -uy, ux
    hw, hd = width_m / 2.0, depth_m / 2.0
    cx, cy = position[0], position[1]
    return Polygon(
        [
            (cx - ux * hw - vx * hd, cy - uy * hw - vy * hd),
            (cx + ux * hw - vx * hd, cy + uy * hw - vy * hd),
            (cx + ux * hw + vx * hd, cy + uy * hw + vy * hd),
            (cx - ux * hw + vx * hd, cy - uy * hw + vy * hd),
        ]
    )


def _asset_footprint(position: list[float], fp: dict[str, Any]) -> Polygon | None:
    try:
        w = float(fp.get("width_m", 0.0))
        d = float(fp.get("depth_m", 0.0))
        rot = float(fp.get("rotation_deg", 0.0))
    except (TypeError, ValueError):
        return None
    if w <= 0 or d <= 0:
        return None
    return footprint_polygon(position, w, d, rot)


def _pair_overlaps(
    polys: list[Polygon], min_fraction: float, min_area: float
) -> list[tuple[int, int, float, float]]:
    """Index pairs (i<j) whose intersection exceeds both thresholds: (i, j, area, fraction)."""
    if len(polys) < 2:
        return []
    tree = STRtree(polys)
    out: list[tuple[int, int, float, float]] = []
    left, right = tree.query(polys, predicate="intersects")
    for i, j in sorted(zip(left.tolist(), right.tolist(), strict=True)):
        if i >= j:
            continue
        area = polys[i].intersection(polys[j]).area
        smaller = min(polys[i].area, polys[j].area)
        if smaller <= 0:
            continue
        frac = area / smaller
        if area > min_area and frac > min_fraction:
            out.append((i, j, area, frac))
    return out


# --- individual checks --------------------------------------------------------------


def _check_duplicate_ids(b: BuildingFile) -> list[Issue]:
    where: dict[str, list[str]] = defaultdict(list)
    groups: list[tuple[str, list[Any]]] = [
        ("floor", b.floors),
        ("space", b.spaces),
        ("opening", b.openings),
        ("connector", b.vertical_connectors),
        ("asset", b.assets),
    ]
    for kind, items in groups:
        for it in items:
            where[it.id].append(kind)
    return [
        _issue("duplicate_id", [i], f"id {i!r} used {len(k)} times ({', '.join(k)})", kinds=k)
        for i, k in where.items()
        if len(k) > 1
    ]


def _check_references(b: BuildingFile) -> list[Issue]:
    floors = {f.id for f in b.floors}
    spaces = {s.id: s for s in b.spaces}
    out: list[Issue] = []

    def bad(eid: str, msg: str, **d: Any) -> None:
        out.append(_issue("dangling_reference", [eid], msg, **d))

    for s in b.spaces:
        if s.floor_id not in floors:
            bad(s.id, f"space {s.id} references unknown floor {s.floor_id}", field="floor_id")
    for o in b.openings:
        if o.floor_id not in floors:
            bad(o.id, f"opening {o.id} references unknown floor {o.floor_id}", field="floor_id")
        for sid in o.space_ids:
            if sid != "outside" and sid not in spaces:
                bad(o.id, f"opening {o.id} references unknown space {sid}", field="space_ids")
    for c in b.vertical_connectors:
        for fid in c.floor_ids:
            if fid not in floors:
                bad(c.id, f"connector {c.id} references unknown floor {fid}", field="floor_ids")
        for sid in c.space_ids:
            if sid not in spaces:
                bad(c.id, f"connector {c.id} references unknown space {sid}", field="space_ids")
        if len(c.floor_ids) != len(c.space_ids):
            bad(
                c.id,
                f"connector {c.id} has {len(c.floor_ids)} floor_ids but {len(c.space_ids)} "
                "space_ids",
                field="floor_ids/space_ids",
            )
        else:
            for fid, sid in zip(c.floor_ids, c.space_ids, strict=True):
                sp = spaces.get(sid)
                if sp is not None and sp.floor_id != fid:
                    bad(
                        c.id,
                        f"connector {c.id} stop {sid} is on floor {sp.floor_id}, not {fid}",
                        field="floor_ids",
                    )
    for a in b.assets:
        if a.floor_id not in floors:
            bad(a.id, f"asset {a.id} references unknown floor {a.floor_id}", field="floor_id")
        sp = spaces.get(a.space_id)
        if sp is None:
            bad(a.id, f"asset {a.id} references unknown space {a.space_id}", field="space_id")
        elif sp.floor_id != a.floor_id:
            bad(
                a.id,
                f"asset {a.id} floor_id {a.floor_id} differs from its space's floor {sp.floor_id}",
                field="floor_id",
                expected=sp.floor_id,
            )
    return out


def _check_polygons(b: BuildingFile) -> list[Issue]:
    out: list[Issue] = []
    for s in b.spaces:
        if len(s.polygon) < 3:
            out.append(_issue("invalid_polygon", [s.id], f"space {s.id} has <3 vertices"))
            continue
        poly = Polygon([(p[0], p[1]) for p in s.polygon])
        if not poly.is_valid:
            from shapely.validation import explain_validity

            out.append(
                _issue(
                    "invalid_polygon",
                    [s.id],
                    f"space {s.id} polygon invalid: {explain_validity(poly)}",
                )
            )
        elif poly.area < MIN_POLYGON_AREA_M2:
            out.append(
                _issue(
                    "invalid_polygon",
                    [s.id],
                    f"space {s.id} area {poly.area:.3f} m2 < {MIN_POLYGON_AREA_M2}",
                    area_m2=_r(poly.area),
                )
            )
    return out


def _check_rotation(b: BuildingFile) -> list[Issue]:
    out: list[Issue] = []
    for a in b.assets:
        rot = a.footprint.get("rotation_deg")
        if isinstance(rot, int | float) and not 0 <= rot < 360:
            out.append(
                _issue(
                    "rotation_out_of_range",
                    [a.id],
                    f"asset {a.id} rotation_deg={rot} outside [0,360)",
                    rotation_deg=rot,
                    suggested_deg=_normalised_deg(rot),
                )
            )
    return out


def _check_name_ref(b: BuildingFile) -> list[Issue]:
    return [
        _issue(
            "name_ref_mismatch",
            [s.id],
            f"space {s.id} name {s.name!r} does not contain ref {s.ref!r}",
            name=s.name,
            ref=s.ref,
        )
        for s in b.spaces
        if s.ref and s.ref not in s.name
    ]


def _check_opening_boundary(b: BuildingFile, polys: dict[str, Polygon]) -> list[Issue]:
    out: list[Issue] = []
    for o in b.openings:
        pt = Point(o.position[0], o.position[1])
        for sid in o.space_ids:
            poly = polys.get(sid)
            if poly is None:
                continue
            dist = poly.boundary.distance(pt)
            if dist > OPENING_BOUNDARY_TOL_M:
                from shapely.ops import nearest_points

                np_ = nearest_points(poly.boundary, pt)[0]
                out.append(
                    _issue(
                        "opening_off_boundary",
                        [o.id, sid],
                        f"opening {o.id} is {dist:.2f} m from the boundary of {sid}",
                        distance_m=_r(dist),
                        nearest_boundary_point=[_r(np_.x), _r(np_.y)],
                        position=o.position,
                    )
                )
    return out


def _check_overlapping_spaces(b: BuildingFile, polys: dict[str, Polygon]) -> list[Issue]:
    by_floor: dict[str, list[str]] = defaultdict(list)
    for s in b.spaces:
        if s.id in polys:
            by_floor[s.floor_id].append(s.id)
    out: list[Issue] = []
    for ids in by_floor.values():
        plist = [polys[i] for i in ids]
        for i, j, area, frac in _pair_overlaps(plist, OVERLAP_FRACTION, OVERLAP_MIN_AREA_M2):
            out.append(
                _issue(
                    "overlapping_spaces",
                    [ids[i], ids[j]],
                    f"spaces {ids[i]} and {ids[j]} overlap by {area:.2f} m2 ({frac:.0%})",
                    overlap_area_m2=_r(area),
                    overlap_fraction=_r(frac),
                )
            )
    return out


def _check_duplicate_space_fields(b: BuildingFile) -> list[Issue]:
    out: list[Issue] = []
    for cls, attr in (("duplicate_space_name", "name"), ("duplicate_space_ref", "ref")):
        groups: dict[str, list[str]] = defaultdict(list)
        for s in b.spaces:
            v = getattr(s, attr)
            if v:
                groups[v].append(s.id)
        for v, ids in groups.items():
            if len(ids) > 1:
                out.append(
                    _issue(
                        cls, ids, f"{len(ids)} spaces share {attr} {v!r}", value=v, count=len(ids)
                    )
                )
    return out


def _stops(b: BuildingFile) -> list[tuple[str, list[tuple[str, str]]]]:
    """Connectors with their (floor_id, space_id) stops (only when lengths agree)."""
    return [
        (c.id, list(zip(c.floor_ids, c.space_ids, strict=True)))
        for c in b.vertical_connectors
        if len(c.floor_ids) == len(c.space_ids)
    ]


def _check_connectors(b: BuildingFile, polys: dict[str, Polygon]) -> list[Issue]:
    spaces = {s.id: s for s in b.spaces}
    floors = {f.id: f for f in b.floors}
    levels = sorted(f.level for f in b.floors)
    out: list[Issue] = []
    for c in b.vertical_connectors:
        if c.kind == "lift":
            for sid in c.space_ids:
                sp = spaces.get(sid)
                if sp is not None and sp.type != "lift":
                    out.append(
                        _issue(
                            "lift_stop_not_lift_space",
                            [c.id, sid],
                            f"lift {c.id} stops at {sid} of type {sp.type!r}",
                            space_type=sp.type,
                        )
                    )
    for cid, stops in _stops(b):
        for (fa, sa), (fb, sb) in zip(stops, stops[1:], strict=False):
            pa, pb = polys.get(sa), polys.get(sb)
            if pa is not None and pb is not None:
                ra, rb = pa.representative_point(), pb.representative_point()
                dist = math.hypot(ra.x - rb.x, ra.y - rb.y)
                if dist > STOP_DISTANCE_TOL_M:
                    out.append(
                        _issue(
                            "connector_stops_far_apart",
                            [cid, sa, sb],
                            f"connector {cid}: stops {sa} and {sb} are {dist:.1f} m apart in XY",
                            distance_m=_r(dist, 2),
                            offset_xy=[_r(rb.x - ra.x), _r(rb.y - ra.y)],
                        )
                    )
            la, lb = floors.get(fa), floors.get(fb)
            if la is not None and lb is not None:
                lo, hi = sorted((la.level, lb.level))
                skipped = [lv for lv in levels if lo < lv < hi]
                if skipped:
                    out.append(
                        _issue(
                            "connector_skips_floor",
                            [cid],
                            f"connector {cid} jumps from level {la.level} to {lb.level}, "
                            f"skipping level(s) {skipped}",
                            skipped_levels=skipped,
                        )
                    )
    return out


def _check_unused_vertical(b: BuildingFile) -> list[Issue]:
    used = {sid for c in b.vertical_connectors for sid in c.space_ids}
    return [
        _issue(
            "unused_vertical_space",
            [s.id],
            f"{s.type} space {s.id} ({s.name}) is used by no connector",
            space_type=s.type,
        )
        for s in b.spaces
        if s.type in ("stair", "lift") and s.id not in used
    ]


def _check_floors(b: BuildingFile) -> list[Issue]:
    out: list[Issue] = []
    fl = sorted(b.floors, key=lambda f: (f.level, f.id))
    for prev, cur in zip(fl, fl[1:], strict=False):
        expected = prev.elevation_m + prev.height_m
        if cur.elevation_m < expected - 1e-6:
            out.append(
                _issue(
                    "overlapping_storeys",
                    [prev.id, cur.id],
                    f"floor {cur.id} elevation {cur.elevation_m} < {prev.id} elevation "
                    f"{prev.elevation_m} + height {prev.height_m}",
                    expected_elevation_m=_r(expected),
                    elevation_m=cur.elevation_m,
                )
            )
    if fl:
        have = {f.level for f in fl}
        missing = [lv for lv in range(fl[0].level, fl[-1].level + 1) if lv not in have]
        if missing:
            out.append(
                _issue(
                    "level_gap",
                    [f.id for f in fl],
                    f"missing floor level(s) {missing}",
                    missing_levels=missing,
                )
            )
    return out


def _check_openings(b: BuildingFile) -> list[Issue]:
    out: list[Issue] = []
    seen: dict[tuple[tuple[str, ...], float, float, str], str] = {}
    for o in b.openings:
        if o.width_m < MIN_OPENING_WIDTH_M:
            out.append(
                _issue(
                    "narrow_opening",
                    [o.id],
                    f"opening {o.id} width {o.width_m} m < {MIN_OPENING_WIDTH_M} m",
                    width_m=o.width_m,
                    suggested_width_m=0.9,
                )
            )
        key = (tuple(sorted(o.space_ids)), _r(o.position[0]), _r(o.position[1]), o.floor_id)
        if key in seen:
            out.append(
                _issue(
                    "duplicate_entrance",
                    [seen[key], o.id],
                    f"openings {seen[key]} and {o.id} join the same spaces at the same position",
                    keep=seen[key],
                    remove=o.id,
                )
            )
        else:
            seen[key] = o.id
    return out


def estimate_floor_offsets(b: BuildingFile) -> dict[str, tuple[float, float]]:
    """Per-floor XY offset (floor minus reference floor) estimated from connector stops.

    Floors are linked through connectors; the offset of a floor pair is the median of the
    stop-point differences over all connectors linking them (pairs whose connectors disagree by
    more than 1 m are ignored: natural stair placement, not a floor shift), then chained from the
    reference floor (lowest level with a connector). Floors not linked to the reference are omitted.
    """
    polys = {s.id: space_polygon(s) for s in b.spaces}
    floors = {f.id: f for f in b.floors}
    pair_vecs: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for _cid, stops in _stops(b):
        for (fa, sa), (fb, sb) in zip(stops, stops[1:], strict=False):
            pa, pb = polys.get(sa), polys.get(sb)
            if pa is None or pb is None or fa == fb or fa not in floors or fb not in floors:
                continue
            ca, cb = pa.centroid, pb.centroid
            pair_vecs[(fa, fb)].append((cb.x - ca.x, cb.y - ca.y))
    adj: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
    for (fa, fb), vecs in pair_vecs.items():
        dx = median(v[0] for v in vecs)
        dy = median(v[1] for v in vecs)
        spread = max(math.hypot(v[0] - dx, v[1] - dy) for v in vecs)
        if math.hypot(dx, dy) > MISALIGNMENT_TOL_M and spread > MISALIGNMENT_SPREAD_M:
            continue  # connectors disagree: natural stair placement, not a global floor shift
        adj[fa].append((fb, dx, dy))
        adj[fb].append((fa, -dx, -dy))
    if not adj:
        return {}
    ref = min(adj, key=lambda f: (floors[f].level, f))
    off: dict[str, tuple[float, float]] = {ref: (0.0, 0.0)}
    queue = [ref]
    while queue:
        cur = queue.pop(0)
        for nxt, dx, dy in sorted(adj[cur]):
            if nxt not in off:
                off[nxt] = (off[cur][0] + dx, off[cur][1] + dy)
                queue.append(nxt)
    return off


def _check_floor_misalignment(b: BuildingFile) -> list[Issue]:
    if len(b.floors) < 2:
        return []
    off = estimate_floor_offsets(b)
    ref = next((f for f, o in off.items() if o == (0.0, 0.0)), None)
    out: list[Issue] = []
    for f in b.floors:
        if f.id not in off:
            continue
        dx, dy = off[f.id]
        if math.hypot(dx, dy) > MISALIGNMENT_TOL_M:
            out.append(
                _issue(
                    "floor_misalignment",
                    [f.id],
                    f"floor {f.id} appears shifted by ({dx:.2f}, {dy:.2f}) m relative to {ref}",
                    reference_floor=ref,
                    offset_xy=[_r(dx), _r(dy)],
                    suggested_dx=_r(-dx),
                    suggested_dy=_r(-dy),
                )
            )
    return out


def _check_assets(b: BuildingFile, polys: dict[str, Polygon]) -> list[Issue]:
    out: list[Issue] = []
    foot: dict[str, Polygon] = {}
    for a in b.assets:
        fp = _asset_footprint(a.position, a.footprint)
        if fp is None:
            continue
        foot[a.id] = fp
        space = polys.get(a.space_id)
        if space is None or fp.area <= 0:
            continue
        outside = fp.difference(space).area / fp.area
        if outside > ASSET_OUTSIDE_FRACTION:
            from shapely.ops import nearest_points

            inside = space.representative_point()
            if space.contains(Point(a.position[0], a.position[1])):
                inside = Point(a.position[0], a.position[1])
            else:
                inside = nearest_points(space, Point(a.position[0], a.position[1]))[0]
            out.append(
                _issue(
                    "asset_outside_space",
                    [a.id, a.space_id],
                    f"asset {a.id}: {outside:.0%} of footprint lies outside {a.space_id}",
                    outside_fraction=_r(outside),
                    suggested_position=[_r(inside.x), _r(inside.y)],
                )
            )
    by_space: dict[str, list[str]] = defaultdict(list)
    for a in b.assets:
        if a.id in foot:
            by_space[a.space_id].append(a.id)
    for ids in by_space.values():
        plist = [foot[i] for i in ids]
        for i, j, area, frac in _pair_overlaps(plist, ASSET_COLLISION_FRACTION, 0.0):
            out.append(
                _issue(
                    "colliding_assets",
                    [ids[i], ids[j]],
                    f"assets {ids[i]} and {ids[j]} overlap by {frac:.0%} of the smaller footprint",
                    overlap_area_m2=_r(area),
                    overlap_fraction=_r(frac),
                )
            )
    return out


def run_checks(building: BuildingFile) -> list[Issue]:
    """Run every check; ordering follows ISSUE_CLASSES, then discovery order."""
    polys = {s.id: p for s in building.spaces if (p := space_polygon(s)) is not None}
    found: list[Issue] = [
        *_check_duplicate_ids(building),
        *_check_references(building),
        *_check_polygons(building),
        *_check_rotation(building),
        *_check_name_ref(building),
        *_check_opening_boundary(building, polys),
        *_check_overlapping_spaces(building, polys),
        *_check_duplicate_space_fields(building),
        *_check_connectors(building, polys),
        *_check_unused_vertical(building),
        *_check_floors(building),
        *_check_openings(building),
        *_check_floor_misalignment(building),
        *_check_assets(building, polys),
    ]
    order = {name: i for i, name in enumerate(ISSUE_CLASSES)}
    unique: dict[str, Issue] = {}
    for i in found:
        unique.setdefault(i.id, i)
    return sorted(unique.values(), key=lambda i: order[i.issue_class])  # stable sort


# --- entity inspection --------------------------------------------------------------


def _poly_summary(s: SpaceIn) -> dict[str, Any]:
    poly = space_polygon(s)
    info: dict[str, Any] = {"vertices": len(s.polygon)}
    if poly is not None:
        minx, miny, maxx, maxy = poly.bounds
        info.update(
            area_m2=_r(poly.area, 2),
            bounds=[_r(minx), _r(miny), _r(maxx), _r(maxy)],
            centroid=[_r(poly.centroid.x), _r(poly.centroid.y)],
        )
    return info


def inspect_entity(building: BuildingFile, entity_id: str) -> dict[str, Any]:
    """Compact view of one entity plus its immediate relations. KeyError for unknown ids."""
    spaces = {s.id: s for s in building.spaces}
    floors = {f.id: f for f in building.floors}

    def space_brief(sid: str) -> dict[str, Any]:
        s = spaces.get(sid)
        if s is None:
            return {"id": sid, "missing": True}
        return {"id": sid, "name": s.name, "type": s.type, "floor_id": s.floor_id}

    if entity_id in floors:
        f = floors[entity_id]
        return {
            "kind": "floor",
            **f.model_dump(),
            "space_count": sum(s.floor_id == f.id for s in building.spaces),
            "opening_count": sum(o.floor_id == f.id for o in building.openings),
            "asset_count": sum(a.floor_id == f.id for a in building.assets),
            "connector_ids": [c.id for c in building.vertical_connectors if f.id in c.floor_ids],
        }
    if entity_id in spaces:
        s = spaces[entity_id]
        fl = floors.get(s.floor_id)
        return {
            "kind": "space",
            **s.model_dump(exclude={"polygon"}),
            **_poly_summary(s),
            "floor": {"id": s.floor_id, "level": fl.level, "name": fl.name} if fl else None,
            "opening_ids": [o.id for o in building.openings if s.id in o.space_ids],
            "asset_count": sum(a.space_id == s.id for a in building.assets),
            "connector_ids": [c.id for c in building.vertical_connectors if s.id in c.space_ids],
        }
    for o in building.openings:
        if o.id == entity_id:
            return {
                "kind": "opening",
                **o.model_dump(),
                "spaces": [space_brief(sid) for sid in o.space_ids if sid != "outside"],
            }
    for c in building.vertical_connectors:
        if c.id == entity_id:
            return {
                "kind": "connector",
                **c.model_dump(),
                "stops": [
                    {"floor_id": fid, **space_brief(sid)}
                    for fid, sid in zip(c.floor_ids, c.space_ids, strict=False)
                ],
            }
    for a in building.assets:
        if a.id == entity_id:
            sp = spaces.get(a.space_id)
            fl = floors.get(a.floor_id)
            return {
                "kind": "asset",
                **a.model_dump(),
                "space": space_brief(a.space_id) if sp else {"id": a.space_id, "missing": True},
                "floor": {"id": a.floor_id, "level": fl.level, "name": fl.name} if fl else None,
            }
    raise KeyError(entity_id)


# --- patch helpers ------------------------------------------------------------------


def translate_floor_ops(
    building: dict[str, Any], floor_id: str, dx: float, dy: float
) -> list[dict[str, Any]]:
    """RFC6902 replace ops shifting all space polygons, openings and assets of a floor."""
    ops: list[dict[str, Any]] = []

    def shift(p: list[float]) -> list[float]:
        return [round(p[0] + dx, 6), round(p[1] + dy, 6), *p[2:]]

    for i, s in enumerate(building.get("spaces", [])):
        if s["floor_id"] == floor_id:
            ops.append(
                {
                    "op": "replace",
                    "path": f"/spaces/{i}/polygon",
                    "value": [shift(p) for p in s["polygon"]],
                }
            )
    for i, o in enumerate(building.get("openings", [])):
        if o["floor_id"] == floor_id:
            ops.append(
                {"op": "replace", "path": f"/openings/{i}/position", "value": shift(o["position"])}
            )
    for i, a in enumerate(building.get("assets", [])):
        if a["floor_id"] == floor_id:
            ops.append(
                {"op": "replace", "path": f"/assets/{i}/position", "value": shift(a["position"])}
            )
    return ops

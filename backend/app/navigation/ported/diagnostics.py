# ruff: noqa: E501, B905, SIM103  (ported prototype code: long messages kept verbatim)
"""Conservative user-facing route failure evidence; never real-world reachability.

Ported unchanged from prototypes/unified-nav-voice/diagnostics.py (imports adapted)."""

import math

import networkx as nx
from shapely.geometry import Point

from app.navigation.errors import ModelError
from app.navigation.ported.geometry import effective_opening, polygon_of, prepare_assets


def annotate_error(building, request, error):
    """Add explanation/element IDs to the same ModelError; retain original code.

    Topology is an optimistic upper bound: geometric paths may be absent even
    where a declared room connection exists. Only disconnections of that upper
    bound certify a particular constraint; otherwise the cause stays unresolved.
    """
    if not isinstance(error, ModelError):
        return error
    # Validation evidence may be cached; never attach request state to it.
    error.details = dict(error.details)
    if "diagnosis" in error.details:
        return error
    elements = []
    category, summary = (
        "unresolved",
        "No supported route found. The exact cause is not established; real unreachability does not follow.",
    )
    clearance = None

    def mark(identifier, reason, kind):
        if isinstance(identifier, str):
            elements.append({"id": identifier, "reason": reason, "category": kind})

    try:
        width, reserve = request.get("width_m", 0.6), request.get("safety_m", 0.05)
        if (
            not isinstance(width, bool)
            and not isinstance(reserve, bool)
            and isinstance(width, (int, float))
            and isinstance(reserve, (int, float))
            and math.isfinite(width + reserve)
        ):
            clearance = width / 2 + reserve
        code, details = error.code, error.details
        if code == "blocked_by_closure":
            category, summary = (
                "closed",
                "The selected space or passage is closed in the in-memory scenario.",
            )
            for spec in (request.get("start", {}), request.get("target", {})):
                if isinstance(spec, dict) and spec.get("id") in request.get("closed", []):
                    mark(spec["id"], "Selected endpoint is closed.", category)
            mark(details.get("space_id"), summary, category)
        elif code == "door_too_narrow":
            category = "too_narrow"
            summary = f"The selected passage is too narrow: {details.get('width_m')} m available, {details.get('required_m')} m required."
            mark(details.get("opening_id"), summary, category)
        elif code in {"geometry_undetermined", "source_drift", "computation_limit"}:
            category, summary = (
                "unsupported",
                "This data source, geometry or computation size is not yet supported by the precision model.",
            )
        elif code == "invalid_input":
            category, summary = (
                "unresolved",
                "The input is invalid or incomplete. Check the endpoints and options.",
            )
        elif code == "blocked_endpoint":
            category, summary = (
                "unreachable_in_model",
                "The selected point is outside the permitted free area for this width and safety margin. It is not moved.",
            )
            mark(details.get("space_id"), summary, category)
            if clearance is not None and details.get("position") is not None:
                location = Point(details["position"])
                room = next(
                    (s for s in building.get("spaces", []) if s["id"] == details.get("space_id")),
                    None,
                )
                forced = {
                    spec.get("id")
                    for spec in (request.get("start", {}), request.get("target", {}))
                    if spec.get("kind") == "asset"
                }
                records, _ = prepare_assets(building, request.get("profile", "stationary"), forced)
                if room:
                    for record, poly in records:
                        if record.get("floor_id") != room["floor_id"]:
                            continue
                        distance = location.distance(poly)
                        if distance < clearance - 1e-7:
                            mark(
                                record["id"],
                                f"Selected point is only {distance:.3f} m from the selected footprint; {clearance:.3f} m required.",
                                category,
                            )
                    wall_distance = location.distance(polygon_of(room).boundary)
                    if wall_distance < clearance - 1e-7:
                        mark(
                            room["id"],
                            f"Wall distance at the point is {wall_distance:.3f} m; {clearance:.3f} m required.",
                            category,
                        )
        elif code == "blocked_by_step_free":
            category, summary = (
                "unreachable_in_model",
                "The selected passage is not marked step-free.",
            )
        elif code == "model_blocked" and error.message == "No nonempty safe location in this room":
            category, summary = (
                "unreachable_in_model",
                "With this width, safety margin and obstacle profile, no permitted free area remains in the selected space.",
            )
            mark(details.get("space_id"), summary, category)
        elif code == "geometry_unsupported" and (
            "reason" in details or "opening_id" in details or "asset_id" in details
        ):
            category, summary = (
                "unsupported",
                "The selected geometry is not sufficiently supported; this does not prove real unreachability.",
            )
            for key in ("opening_id", "asset_id", "space_id"):
                mark(details.get(key), summary, category)
        elif code in {"model_blocked", "geometry_unsupported", "portal_blocked"}:
            spaces = {s["id"]: s for s in building.get("spaces", [])}
            assets = {a["id"]: a for a in building.get("assets", [])}

            def endpoint(spec):
                if spec.get("kind") in {"point", "door"}:
                    return spec.get("space_id")
                if spec.get("kind") == "asset":
                    return assets.get(spec.get("id"), {}).get("space_id")
                return spec.get("id")

            start, target = endpoint(request.get("start", {})), endpoint(request.get("target", {}))
            if start in spaces and target in spaces:
                base = nx.Graph()
                base.add_nodes_from(spaces)

                def add(graph, a, b, identifier):
                    if a not in spaces or b not in spaces:
                        return
                    if graph.has_edge(a, b):
                        graph[a][b]["ids"].add(identifier)
                    else:
                        graph.add_edge(a, b, ids={identifier})

                for opening in building.get("openings", []):
                    sides = opening.get("space_ids", [])
                    if len(sides) == 2 and (
                        not request.get("step_free") or opening.get("step_free", False)
                    ):
                        add(base, *sides, opening["id"])
                if request.get("allow_schematic_vertical"):
                    for connector in building.get("vertical_connectors", []):
                        if request.get("step_free") and not connector.get("step_free", False):
                            continue
                        levels = {f["id"]: f["level"] for f in building["floors"]}
                        stops = sorted(
                            zip(connector["floor_ids"], connector["space_ids"], strict=True),
                            key=lambda stop: levels[stop[0]],
                        )
                        for (_, a), (_, b) in zip(stops, stops[1:]):
                            add(base, a, b, connector["id"])

                def connected(graph):
                    return start in graph and target in graph and nx.has_path(graph, start, target)

                def constrained(graph, blocked):
                    filtered = graph.copy()
                    filtered.remove_nodes_from(blocked & spaces.keys())
                    for a, b, attrs in list(filtered.edges(data=True)):
                        attrs["ids"] = attrs["ids"] - blocked
                        if not attrs["ids"]:
                            filtered.remove_edge(a, b)
                    return filtered

                def restoration_group(graph, blocked):
                    # An inclusion-minimal group restoring optimistic topology,
                    # not a claim that each marked element alone is the cause.
                    restored = set(blocked)
                    for identifier in sorted(blocked):
                        candidate = restored - {identifier}
                        if connected(constrained(graph, set(blocked) - candidate)):
                            restored = candidate
                    return sorted(restored)

                closed = set(request.get("closed", []))
                narrowed = base.copy()
                narrowed.remove_nodes_from(closed & spaces.keys())
                for a, b, attrs in list(narrowed.edges(data=True)):
                    attrs["ids"] = attrs["ids"] - closed
                    if not attrs["ids"]:
                        narrowed.remove_edge(a, b)
                if connected(base) and not connected(narrowed):
                    category, summary = (
                        "closed",
                        "The closures already cut the declared indoor connection between the spaces. A real outdoor alternative is not assessed.",
                    )
                    for identifier in restoration_group(base, closed):
                        mark(
                            identifier,
                            "Part of a group whose reopening restores the indoor topology; not a physical route clearance.",
                            category,
                        )
                elif clearance is not None and connected(narrowed):
                    small = {}
                    for original in building.get("openings", []):
                        physical = effective_opening(building, original)
                        if physical["width_m"] < 2 * clearance - 1e-7:
                            small[original["id"]] = physical["width_m"]
                    wide = narrowed.copy()
                    for a, b, attrs in list(wide.edges(data=True)):
                        attrs["ids"] = attrs["ids"] - small.keys()
                        if not attrs["ids"]:
                            wide.remove_edge(a, b)
                    if not connected(wide):
                        category, summary = (
                            "too_narrow",
                            f"Every declared indoor connection needs at least one passage that is too narrow; required width incl. margin: {2 * clearance:.3f} m.",
                        )
                        for identifier in restoration_group(narrowed, set(small)):
                            mark(
                                identifier,
                                f"{small[identifier]:.3f} m available; {2 * clearance:.3f} m required. Part of the relevant group of bottlenecks in the indoor topology.",
                                category,
                            )
                    else:
                        unsupported = {
                            x["id"]
                            for x in details.get("unsupported_transitions", [])
                            if x.get("code") in {"geometry_unsupported", "outside_not_physical"}
                        }
                        known = wide.copy()
                        for a, b, attrs in list(known.edges(data=True)):
                            attrs["ids"] = attrs["ids"] - unsupported
                            if not attrs["ids"]:
                                known.remove_edge(a, b)
                        if unsupported and not connected(known):
                            category, summary = (
                                "unsupported",
                                "The declared space sequence needs unsupported transitions. Whether they are physically passable is not established.",
                            )
                            for identifier in restoration_group(wide, unsupported):
                                mark(
                                    identifier,
                                    "Part of the relevant group of unsupported transitions in the indoor topology; not a physical route clearance.",
                                    category,
                                )
                elif not connected(base):
                    category, summary = (
                        "unreachable_in_model",
                        "The current model has no declared indoor connection between these endpoints. No statement about the real building.",
                    )
            if category == "unresolved":
                for item in details.get("unsupported_transitions", []):
                    mark(
                        item.get("id"),
                        "Relevance for this route unclear: "
                        + item.get("message", "Unsupported transition."),
                        "unresolved",
                    )
            if code == "portal_blocked":
                mark(
                    details.get("opening_id"),
                    "Door approach blocked by an obstacle or wall margin.",
                    "unresolved",
                )
        else:
            category, summary = (
                "unresolved",
                "The calculation failed; a program or geometry problem is possible. Unreachability is not established.",
            )
    except (KeyError, TypeError, ValueError, nx.NetworkXException):
        elements.clear()
        category, summary = (
            "unresolved",
            "The exact cause of the failure could not be classified reliably. Real unreachability is not established.",
        )
    error.details["diagnosis"] = {
        "category": category,
        "summary": summary,
        "elements": elements[:100],
        "clearance_m": clearance,
    }
    return error

"""Stage 0: rebuild building.json from floor plans when the dataset has none.

Floors with a kit SVG are parsed exactly (`tools.svgplan`) and assembled with
equipment.csv (`tools.reconstruct`). A vision tracer for floors with only a PNG/JPG
(`trace_floor`) exists but is switched off (`IMAGE_TRACING`). What no source holds (floor heights,
room types of empty rooms, door widths) is defaulted; online, an agent looks at the plan
images and fills those gaps. The result is written into the dataset folder as
building.json, with `reconstruction.json` recording how, so every later stage, ingestion
and the browser preview treat it like an uploaded file.
"""

import json
import re
from pathlib import Path
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, Field, ValidationError

from app.pipeline.agents.base import (
    DATASET_IMAGE_NOTE,
    AgentError,
    StageContext,
    agent_tool,
    as_json,
    dataset_json,
    image_block,
    retry_note,
    run_structured,
    run_subagent,
)
from app.pipeline.state import Deviation, StageReport, Usage
from app.pipeline.store import write_json
from app.pipeline.tools.reconstruct import (
    Overrides,
    Reconstruction,
    assemble,
    read_equipment,
)
from app.pipeline.tools.svgplan import (
    PlanFloor,
    PlanOpening,
    PlanSpace,
    Transform,
    parse_floor_svg,
)
from navgraph.builder import BuildingBuilder
from navgraph.model import Building

BUILDING_FILE = "building.json"
PROVENANCE_FILE = "reconstruction.json"
REPORT_FILE = "reports/reconstruction.json"
# Tracing PNG/JPG-only floors with `trace_floor` is kept but disconnected: too inexact for now,
# so only SVG floors are reconstructed (uploads also count only SVGs as reconstructable).
IMAGE_TRACING = False
PLAN_FILE = re.compile(r"^floor_(-?\d+)(_plain)?\.(svg|png|jpe?g)$", re.I)

TRACE_PROMPT = """\
You trace one floor of a building from its floor plan image into structured data. Return
every enclosed space as a polygon in image pixel coordinates (origin top-left, x right,
y down), with its type: room, hall (large open zone or lobby), corridor, stair, lift or
outdoor; plus a room_type in snake_case (office, meeting_room, toilet, kitchen, bedroom,
living_room, bathroom, storage, corridor, stairwell, lift, lobby, ...) and a short name
when a label shows one. Neighbouring spaces must share walls (no gaps, no overlaps).
Mark every door, wide opening and building entrance as a point on the wall it pierces.
Estimate metres_per_px from the scale bar, dimensions or typical sizes (a door is about
0.9 m wide, an office about 3-5 m deep). At least one entrance is required."""

GAP_PROMPT = """\
You complete a building reconstructed from floor plan drawings. The geometry, space types
and door positions are exact; some facts are not drawn and were defaulted. `list_gaps`
shows them. Look at the plan images (`view_floor`) and fix what the drawings let you
decide: room types of unlabeled rooms (from fixtures, size, position, neighbours), floor
heights (only if the plan states them; otherwise keep the 3 m default), and widths of
doors whose drawn opening is clearly wider or narrower than 0.9 m. Do not guess wildly:
leaving a sensible default is fine. Finish with a short report."""


class TracedSpace(BaseModel):
    type: Literal["room", "hall", "corridor", "stair", "lift", "outdoor"]
    room_type: str
    name: str = ""
    polygon_px: list[list[float]] = Field(description="[[x, y], ...] in image pixels, >= 3 points")


class TracedOpening(BaseModel):
    kind: Literal["door", "opening", "entrance"]
    position_px: list[float] = Field(description="[x, y] in image pixels")


class TracedFloor(BaseModel):
    floor_name: str = ""
    building_name: str = ""
    metres_per_px: float = Field(description="Scale estimate, metres per image pixel; > 0")
    spaces: list[TracedSpace]
    openings: list[TracedOpening]


class GapReport(BaseModel):
    changed: list[str] = Field(default_factory=list, description="What was set, one line each.")
    left_default: list[str] = Field(
        default_factory=list, description="Gaps kept at their default and why."
    )


def plan_files(dataset_dir: Path) -> dict[int, dict[str, Path]]:
    """Floor plan files by level: `svg`, and `image` (the plain variant preferred)."""
    by_level: dict[int, dict[str, Path]] = {}
    for path in sorted(dataset_dir.iterdir()):
        match = PLAN_FILE.match(path.name)
        if not match:
            continue
        files = by_level.setdefault(int(match[1]), {})
        if match[3].lower() == "svg":
            files["svg"] = path
        elif match[2] or "image" not in files:
            files["image"] = path
    return by_level


def is_reconstructed(dataset_dir: Path) -> bool:
    return (dataset_dir / PROVENANCE_FILE).is_file()


def trace_floor(ctx: StageContext, level: int, image: Path) -> tuple[PlanFloor, Usage]:
    assert ctx.client is not None
    with Image.open(image) as picture:
        width, height = picture.size
    content = image_block(
        image, f"Floor plan, level {level}, {width}×{height} px ({DATASET_IMAGE_NOTE})"
    )
    content.append({"type": "text", "text": f"Trace floor level {level}.{retry_note(ctx)}"})
    traced, usage = run_structured(
        ctx.client,
        name=f"reconstruct_trace_{level}",
        model=ctx.models.validator,
        system=TRACE_PROMPT,
        user=content,
        output=TracedFloor,
        effort="high",
    )
    k = 1 / traced.metres_per_px if traced.metres_per_px > 0 else 10.0
    floor = PlanFloor(
        level=level,
        source="vision",
        spaces=[
            PlanSpace(
                [(p[0], p[1]) for p in s.polygon_px if len(p) >= 2], s.type, s.room_type, s.name
            )
            for s in traced.spaces
            if len(s.polygon_px) >= 3
        ],
        openings=[
            PlanOpening((o.position_px[0], o.position_px[1]), o.kind)
            for o in traced.openings
            if len(o.position_px) >= 2
        ],
        building_name=traced.building_name or None,
        floor_name=traced.floor_name or None,
        # y up from the image's bottom edge, like the kit's metre frame.
        transform=Transform(k, 0.0, float(height)),
    )
    return floor, usage


def check(reconstruction: Reconstruction) -> list[dict[str, Any]]:
    """navgraph's building checks (entrance, reachability, connectors, door placement)."""
    return BuildingBuilder(Building.from_dict(reconstruction.building.model_dump())).validate()


def fill_gaps(
    ctx: StageContext,
    floors: list[PlanFloor],
    rows: list[dict[str, str]],
    overrides: Overrides,
    first: Reconstruction,
    files: dict[int, dict[str, Path]],
    source_ref: dict[str, Any],
) -> tuple[Reconstruction, GapReport, Usage]:
    assert ctx.client is not None
    state = {"current": first}

    def rebuild() -> Reconstruction:
        state["current"] = assemble(ctx.location_id, floors, rows, overrides, source_ref)
        return state["current"]

    def space(space_id: str) -> dict[str, Any] | None:
        return next(
            (s.model_dump() for s in state["current"].building.spaces if s.id == space_id), None
        )

    @agent_tool
    def list_gaps() -> str:
        """Facts the drawings don't hold that were defaulted, plus the reconstruction stats."""
        return dataset_json({"gaps": state["current"].gaps, "stats": state["current"].stats})

    @agent_tool
    def view_floor(level: int) -> list[Any]:
        """The dataset's plan image of one floor.

        Args:
            level: Floor level, e.g. 0.
        """
        image = files.get(level, {}).get("image")
        if image is None:
            return [
                {"type": "text", "text": f"No plan image for level {level}; have {sorted(files)}"}
            ]
        return image_block(image, f"Dataset plan, level {level} ({DATASET_IMAGE_NOTE})")

    @agent_tool
    def get_space(space_id: str) -> str:
        """One reconstructed space (type, room_type, name, ref, polygon in metres).

        Args:
            space_id: Space id from list_gaps.
        """
        found = space(space_id)
        return dataset_json(found) if found else f"Unknown space {space_id}"

    @agent_tool
    def set_space(space_id: str, room_type: str, name: str) -> str:
        """Set a space's room_type (snake_case) and name ("" keeps the generated name).

        Args:
            space_id: Space id.
            room_type: e.g. office, toilet, storage, lobby.
            name: Display name, or "" to derive it from room_type and ref.
        """
        if space(space_id) is None:
            return f"Unknown space {space_id}"
        overrides.spaces[space_id] = {"room_type": room_type} | ({"name": name} if name else {})
        rebuild()
        return as_json(space(space_id))

    @agent_tool
    def set_floor(level: int, elevation_m: float, height_m: float) -> str:
        """Set a floor's elevation and storey height in metres.

        Args:
            level: Floor level.
            elevation_m: Height of the floor slab above ground floor.
            height_m: Storey height, > 0.
        """
        if height_m <= 0:
            return "height_m must be > 0"
        overrides.floors[level] = {"elevation_m": elevation_m, "height_m": height_m}
        rebuild()
        return "ok"

    @agent_tool
    def set_opening_width(opening_id: str, width_m: float) -> str:
        """Set the clear width of one door/opening in metres.

        Args:
            opening_id: Opening id.
            width_m: Width in metres, 0.5-50.
        """
        if not 0.5 <= width_m <= 50:
            return "width_m must be between 0.5 and 50"
        overrides.opening_widths[opening_id] = width_m
        rebuild()
        return "ok"

    @agent_tool
    def set_scale(level: int, metres_per_px: float) -> str:
        """Correct the guessed scale of a floor drawn without equipment positions.

        Args:
            level: Floor level.
            metres_per_px: Metres per drawing pixel, > 0.
        """
        if metres_per_px <= 0:
            return "metres_per_px must be > 0"
        overrides.scales[level] = metres_per_px
        rebuild()
        return as_json(state["current"].stats)

    report, usage = run_subagent(
        ctx.client,
        name="reconstruct",
        model=ctx.models.worker,
        system=GAP_PROMPT,
        user=f"Fill the gaps of the reconstruction of {ctx.location_id}.{retry_note(ctx)}",
        tools=[
            list_gaps,
            view_floor,
            get_space,
            set_space,
            set_floor,
            set_opening_width,
            set_scale,
        ],
        output=GapReport,
        max_iterations=20,
        effort="low",
    )
    return state["current"], report, usage


def run(ctx: StageContext) -> StageReport:
    files = plan_files(ctx.dataset_dir)
    if not files:
        return _failed(
            ctx, "No floor_<level>.svg/.png/.jpg in the dataset; nothing to reconstruct."
        )
    rows = read_equipment(ctx.dataset_dir / "equipment.csv")
    usage = Usage()
    floors: list[PlanFloor] = []
    for level, found in sorted(files.items()):
        if "svg" in found:
            floors.append(parse_floor_svg(found["svg"], level))
        elif not IMAGE_TRACING:
            return _failed(
                ctx,
                f"Floor {level} has only an image ({found['image'].name}); reconstructing from "
                "images is switched off for now. Upload floor_<level>.svg or building.json.",
            )
        elif ctx.offline:
            return _failed(
                ctx,
                f"Floor {level} has only an image ({found['image'].name}); tracing it needs an "
                "API key. Upload floor_<level>.svg or building.json instead.",
            )
        else:
            try:
                floor, used = trace_floor(ctx, level, found["image"])
            except AgentError as exc:
                return _failed(ctx, f"Tracing floor {level} failed: {exc}")
            floors.append(floor)
            usage = usage.add(used)

    source_ref = {
        "dataset": ctx.dataset_dir.name,
        "files": sorted(p.name for f in files.values() for p in f.values())
        + (["equipment.csv"] if rows else []),
    }
    overrides = Overrides()
    try:
        result = assemble(ctx.location_id, floors, rows, overrides, source_ref)
    except (ValidationError, ValueError) as exc:
        return _failed(ctx, f"Could not assemble a building from the plans: {exc}")
    report = GapReport()
    if not ctx.offline and result.gaps:
        try:
            result, report, used = fill_gaps(
                ctx, floors, rows, overrides, result, files, source_ref
            )
            usage = usage.add(used)
        except AgentError as exc:  # the defaults stand; the gaps stay reported
            report.left_default.append(f"gap agent failed: {exc}")

    issues = check(result)
    errors = [i for i in issues if i["severity"] == "error"]
    (ctx.dataset_dir / BUILDING_FILE).write_text(
        json.dumps(
            result.building.model_dump(exclude_defaults=False), indent=2, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    provenance = {
        "methods": {str(f.level): f.source for f in floors},
        "stats": result.stats,
        "gaps": result.gaps,
        "agent": report.model_dump(),
        "overrides": {
            "floors": overrides.floors,
            "spaces": overrides.spaces,
            "opening_widths": overrides.opening_widths,
            "scales": overrides.scales,
        },
        "checks": issues[:200],
    }
    write_json(ctx.dataset_dir / PROVENANCE_FILE, provenance)
    write_json(ctx.path(REPORT_FILE), provenance)
    traced = sorted(f.level for f in floors if f.source == "vision")
    stats = result.stats
    return StageReport(
        stage="reconstruct",
        status="failed" if errors else "passed",
        summary=f"Reconstructed {stats['floors']} floors, {stats['spaces']} spaces, "
        f"{stats['openings']} openings, {stats['vertical_connectors']} connectors and "
        f"{stats['assets']} assets from floor plans"
        + (f" (traced from images: levels {traced})" if traced else "")
        + f"; {len(result.gaps)} gaps, {len(errors)} check errors.",
        metrics={**stats, "gaps": len(result.gaps), "check_errors": len(errors)},
        open_issues=[f"gap {g['kind']} {g['target']}: {g['detail']}" for g in result.gaps][:15]
        + report.left_default[:5],
        deviations=[
            Deviation(
                stage="reconstruct",
                kind="graph_check",
                entity_id=i["element"],
                message=i["problem"],
            )
            for i in errors[:30]
        ],
        artifacts=[REPORT_FILE],
        usage=usage,
        attempt=ctx.attempt,
    )


def _failed(ctx: StageContext, message: str) -> StageReport:
    return StageReport(
        stage="reconstruct",
        status="failed",
        summary=message,
        deviations=[Deviation(stage="reconstruct", kind="no_input", message=message)],
        attempt=ctx.attempt,
    )

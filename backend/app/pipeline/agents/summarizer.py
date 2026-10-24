"""Stage 4: populate the location's metadata (building, floors, spaces).

Facts are computed deterministically (`tools.stats`); models only write the prose.
The building and floor summaries come from one worker call; space descriptions come
from the cheap summary model in parallel chunks. Offline, templates write the text.
"""

from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field

from app.pipeline.agents.base import (
    AgentError,
    StageContext,
    dataset_json,
    run_structured,
    untrusted,
)
from app.pipeline.state import StageReport, Usage
from app.pipeline.store import FloorMeta, LocationMetadata, SpaceMeta
from app.pipeline.tools.stats import LocationStats, SpaceStats, compute_location_stats, space_brief

CHUNK = 60
MAX_PARALLEL = 4
BUILDING_PROMPT = """\
You write the reference description of a building for a "talk to your building"
assistant, like a site guide introducing it, not a data sheet.
Summary: two or three sentences, at most 60 words. Open with what kind of place it is
and what it is equipped to do, read from its machines and zones, e.g. "Werk Nord
is a two-storey production site built for CNC machining and injection moulding, with a
dedicated 3D-printing area." Then how it is laid out: halls, office and support areas,
stairs, lifts and entrances. Use at most two numbers; the app shows the counts already.
Key facts: 3-6 short items; these may carry numbers.
Floors: one sentence each (at most 20 words) on what that floor is for and what is there.
Name things in plain words (CNC milling, not cnc_mill). Say only what the facts support:
no superlatives, no guesses about owners, products or output."""
SPACES_PROMPT = """\
For each space line, write one sentence (at most 25 words) that says what the space is
set up for, read from its kind and equipment, e.g. "Meeting room for about ten people
with a display screen and whiteboard; step-free." Mention size only when it stands out.
Plain words (display screen, not display_screen); use only the facts on the line.
The first field of a line is its space_id: return every space_id exactly as given,
exactly once."""


class FloorText(BaseModel):
    floor_id: str
    description: str


class BuildingText(BaseModel):
    summary: str
    key_facts: list[str]
    floors: list[FloorText]


class SpaceText(BaseModel):
    space_id: str
    description: str


class SpaceTexts(BaseModel):
    items: list[SpaceText] = Field(default_factory=list)


def _template(space: SpaceStats) -> str:
    equipment = ", ".join(
        f"{n}x {t.replace('_', ' ')}" for t, n in list(space.equipment.items())[:4]
    )
    access = "step-free" if space.step_free_access else "not step-free"
    text = f"{space.name} is a {space.room_type.replace('_', ' ')} of {space.area_m2:.0f} m²"
    return f"{text} with {equipment}; {access}." if equipment else f"{text}; {access}."


# Offline wording: what a zone is used for and what a machine can do.
ROOM_WORDS = {
    "production_zone": "production",
    "assembly_zone": "assembly",
    "additive_zone": "additive manufacturing",
    "logistics_zone": "logistics",
    "warehouse_zone": "warehousing",
    "quality_lab": "quality inspection",
    "office": "offices",
    "meeting_room": "meeting rooms",
    "server_room": "server rooms",
    "kitchen": "kitchens",
    "locker_room": "locker rooms",
}
WORK_ZONES = {"production_zone", "assembly_zone", "additive_zone", "logistics_zone"}
SHOP_FLOOR = WORK_ZONES | {"warehouse_zone", "quality_lab"}
# Room types that every building has; they say nothing about what it is for.
BACKGROUND_ROOMS = {"corridor", "lobby", "toilet", "stairwell", "lift", "hall", "room"}
MACHINE_WORDS = {
    "cnc_mill": "CNC milling",
    "cnc_lathe": "CNC turning",
    "drill_press": "drilling",
    "injection_moulding_machine": "injection moulding",
    "industrial_3d_printer": "3D printing",
    "3d_printer": "3D printing",
    "waterjet_cutter": "waterjet cutting",
    "laser_cutter": "laser cutting",
    "curing_oven": "curing",
    "coordinate_measuring_machine": "precision measuring",
}
# Machines that support the work rather than define it.
SUPPORT_MACHINES = {"conveyor", "forklift", "compressor"}
STOREYS = {1: "single-storey", 2: "two-storey", 3: "three-storey", 4: "four-storey"}


def _yes(flag: bool) -> str:
    return "yes" if flag else "no"


def _join(words: list[str]) -> str:
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"


def _uses(area_by_room_type: dict[str, float], limit: int = 3) -> list[str]:
    """What the largest rooms are for, biggest first, e.g. ['warehousing', 'production']."""
    ranked = sorted(area_by_room_type.items(), key=lambda kv: -kv[1])
    words = [ROOM_WORDS.get(t, t.replace("_", " ")) for t, _ in ranked if t not in BACKGROUND_ROOMS]
    return list(dict.fromkeys(words))[:limit]


def _capabilities(machines: dict[str, int], limit: int = 4) -> list[str]:
    ranked = sorted(machines.items(), key=lambda kv: -kv[1])
    words = [
        MACHINE_WORDS.get(t, t.replace("_", " ")) for t, _ in ranked if t not in SUPPORT_MACHINES
    ]
    return list(dict.fromkeys(words))[:limit]


def _building_template(stats: LocationStats) -> BuildingText:
    storeys = STOREYS.get(stats.floor_count, f"{stats.floor_count}-storey")
    by_type: dict[str, float] = {}
    for f in stats.floors:
        for t, area in f.area_by_room_type.items():
            by_type[t] = by_type.get(t, 0) + area
    if WORK_ZONES & by_type.keys():
        # Lead with the shop floor; offices and the like follow as support.
        main = _uses({t: a for t, a in by_type.items() if t in SHOP_FLOOR})
        rest = _uses({t: a for t, a in by_type.items() if t not in SHOP_FLOOR}, limit=2)
        opening = f"{stats.name} is a {storeys} production site for {_join(main)}"
        opening += f", plus {_join(rest)}." if rest else "."
    else:
        uses = _uses(by_type)
        opening = f"{stats.name} is a {storeys} building" + (
            f" with {_join(uses)}." if uses else "."
        )
    sentences = [opening]
    capabilities = _capabilities(stats.machines)
    if capabilities:
        sentences.append(f"Its machines cover {_join(capabilities)}.")
    links = [w for w, has in (("stairs", stats.has_stairs), ("a lift", stats.has_lift)) if has]
    if stats.floor_count > 1 and links:
        verb = "connect" if "stairs" in links else "connects"
        sentences.append(f"{_join(links).capitalize()} {verb} the floors.")
    if stats.has_step_free_entrance:
        sentences.append("The entrance is step-free.")
    return BuildingText(
        summary=" ".join(sentences),
        key_facts=[
            f"{stats.total_area_m2:.0f} m² over {stats.floor_count} floors",
            f"{stats.space_count} spaces, {stats.asset_count} assets",
            f"{sum(stats.machines.values())} machines",
            f"Lift: {_yes(stats.has_lift)}; stairs: {_yes(stats.has_stairs)}",
            f"Step-free entrance: {_yes(stats.has_step_free_entrance)}",
        ],
        floors=[
            FloorText(
                floor_id=f.floor_id,
                description=(
                    f"{f.name}: {_join(floor_uses)}, across {f.space_count} spaces."
                    if (floor_uses := _uses(f.area_by_room_type))
                    else f"{f.name}: {f.space_count} spaces, {f.area_m2:.0f} m²."
                ),
            )
            for f in stats.floors
        ],
    )


def run(ctx: StageContext) -> StageReport:
    building = ctx.building()
    stats = compute_location_stats(building)
    texts = {sid: _template(s) for sid, s in stats.spaces.items()}
    building_text = _building_template(stats)
    usage = Usage()
    notes: list[str] = []

    if not ctx.offline:
        assert ctx.client is not None
        client = ctx.client
        overview = stats.model_dump(mode="json", exclude={"spaces"})
        try:
            building_text, usage = run_structured(
                client,
                name="summarizer:building",
                model=ctx.models.worker,
                system=BUILDING_PROMPT,
                user=f"Facts:\n{dataset_json(overview, limit=60_000)}",
                output=BuildingText,
            )
        except AgentError as exc:
            notes.append(f"Building summary fell back to the template: {exc}")
        briefs = [space_brief(s) for s in stats.spaces.values()]
        # Briefs start with a short id; map the model's answers back to full ids.
        full_id = {
            brief.split(" | ", 1)[0]: sid for brief, sid in zip(briefs, stats.spaces, strict=True)
        }
        described: set[str] = set()
        chunks = [briefs[i : i + CHUNK] for i in range(0, len(briefs), CHUNK)]

        def describe(chunk: list[str]) -> tuple[SpaceTexts, Usage]:
            try:
                return run_structured(
                    client,
                    name="summarizer:spaces",
                    model=ctx.models.summary,
                    system=SPACES_PROMPT,
                    user=untrusted("\n".join(chunk)),
                    output=SpaceTexts,
                )
            except AgentError as exc:
                notes.append(f"A chunk of space descriptions fell back to templates: {exc}")
                return SpaceTexts(), Usage()

        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
            for result, chunk_usage in pool.map(describe, chunks):
                usage = usage.add(chunk_usage)
                for item in result.items:
                    sid = full_id.get(item.space_id.strip(), item.space_id.strip())
                    if sid in texts and item.description.strip():
                        texts[sid] = item.description.strip()
                        described.add(sid)
        if len(described) < len(texts):
            notes.append(
                f"{len(texts) - len(described)} of {len(texts)} space descriptions "
                "fell back to templates."
            )

    issues_by_entity: dict[str, list[str]] = {}
    for issue in ctx.workspace.locations.read_issues(ctx.location_id):
        if issue.status != "fixed":
            for entity_id in issue.entity_ids:
                issues_by_entity.setdefault(entity_id, []).append(issue.message)

    floor_text = {f.floor_id: f.description for f in building_text.floors}
    metadata = LocationMetadata(
        summary=building_text.summary,
        key_facts=building_text.key_facts,
        stats=stats.model_dump(mode="json", exclude={"spaces", "floors"}),
        floors=[
            FloorMeta(
                floor_id=f.floor_id,
                level=f.level,
                name=f.name,
                description=floor_text.get(f.floor_id, ""),
                stats=f.model_dump(mode="json"),
            )
            for f in stats.floors
        ],
        spaces={
            sid: SpaceMeta(
                space_id=sid,
                floor_id=s.floor_id,
                name=s.name,
                ref=s.ref,
                type=s.type,
                room_type=s.room_type,
                area_m2=s.area_m2,
                description=texts[sid],
                equipment=s.equipment,
                doors=s.doors,
                step_free_access=s.step_free_access,
                issues=issues_by_entity.get(sid, []),
            )
            for sid, s in stats.spaces.items()
        },
    )
    record = ctx.workspace.locations.get(ctx.location_id)
    if record is not None:
        record.metadata = metadata
        ctx.workspace.locations.save(record)

    return StageReport(
        stage="summarize",
        status="passed",
        summary=(
            f"Metadata written for {len(metadata.spaces)} spaces on {len(metadata.floors)} floors."
        ),
        metrics={"spaces": len(metadata.spaces), "key_facts": len(metadata.key_facts)},
        open_issues=notes,
        artifacts=["location.json"],
        usage=usage,
    )

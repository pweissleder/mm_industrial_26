"""Stage 4: populate the location's metadata (building, floors, spaces).

Facts are computed deterministically (`tools.stats`); models only write the prose.
The building and floor summaries come from one worker call; space descriptions come
from the cheap summary model in parallel chunks. Offline, templates write the text.
"""

from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field

from app.pipeline.agents.base import AgentError, StageContext, as_json, run_structured
from app.pipeline.state import StageReport, Usage
from app.pipeline.store import FloorMeta, LocationMetadata, SpaceMeta
from app.pipeline.tools.stats import LocationStats, SpaceStats, compute_location_stats, space_brief

CHUNK = 60
MAX_PARALLEL = 4
BUILDING_PROMPT = """\
You write the reference description of a building for a "talk to your building"
assistant. From the facts given, write a concise summary (what kind of building, size,
floors, notable areas and equipment, accessibility), 4-8 key facts, and one short
description per floor. Use only the facts given; no speculation."""
SPACES_PROMPT = """\
For each space line, write one factual sentence describing the space (what it is, its
size, notable equipment, access). Use only the facts on the line. The first field of a
line is its space_id: return every space_id exactly as given, exactly once."""


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


def _yes(flag: bool) -> str:
    return "yes" if flag else "no"


def _building_template(stats: LocationStats) -> BuildingText:
    return BuildingText(
        summary=(
            f"{stats.name}: {stats.floor_count} floors, {stats.total_area_m2:.0f} m² over "
            f"{stats.space_count} spaces with {stats.asset_count} assets."
        ),
        key_facts=[
            f"Levels: {stats.levels}",
            f"Lift: {_yes(stats.has_lift)}; stairs: {_yes(stats.has_stairs)}",
            f"Step-free entrance: {_yes(stats.has_step_free_entrance)}",
        ],
        floors=[
            FloorText(
                floor_id=f.floor_id,
                description=f"{f.name}: {f.space_count} spaces, {f.area_m2:.0f} m².",
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
                user=f"Facts:\n{as_json(overview, limit=60_000)}",
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
                    user="\n".join(chunk),
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

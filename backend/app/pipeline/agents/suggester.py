"""Stage 5: three starter questions for the chat assistant, fitted to this building.

Online, one cheap call writes them from the computed facts; offline (or if that call
fails or returns something unusable) a template fills in real room, machine and floor names.
"""

from pydantic import BaseModel

from app.pipeline.agents.base import (
    AgentError,
    StageContext,
    dataset_json,
    run_structured,
    untrusted,
)
from app.pipeline.agents.summarizer import (
    BACKGROUND_ROOMS,
    MACHINE_WORDS,
    SUPPORT_MACHINES,
    WORK_ZONES,
)
from app.pipeline.state import StageReport, Usage
from app.pipeline.store import LocationMetadata
from app.pipeline.tools.stats import LocationStats, compute_location_stats

COUNT = 3
MAX_LEN = 70
PROMPT = """\
You write three starter questions that a visitor would type to the assistant of THIS
building. Each at most 70 characters, in plain English, no numbering.
Mix the kinds: (1) find one specific notable machine or room by its name, (2) a route
between two real, named rooms, (3) a floor or accessibility question.
Use only names that appear in the facts, exactly as written there."""


class Suggestions(BaseModel):
    questions: list[str]


def clean(items: list[str]) -> list[str]:
    """Trimmed, non-empty, short, de-duplicated (case-insensitive), at most COUNT."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = " ".join(item.split())
        if text and len(text) <= MAX_LEN and text.lower() not in seen:
            seen.add(text.lower())
            out.append(text)
    return out[:COUNT]


def template_suggestions(stats: LocationStats) -> list[str]:
    spaces = sorted(stats.spaces.values(), key=lambda s: (-s.area_m2, s.name))
    named = [s for s in spaces if s.name.strip()]
    candidates: list[str] = []

    machines = sorted(
        ((t, n) for t, n in stats.machines.items() if t not in SUPPORT_MACHINES),
        key=lambda kv: (-kv[1], kv[0]),
    )
    if machines:
        kind = machines[0][0]
        word = MACHINE_WORDS.get(kind, kind.replace("_", " "))
        candidates.append(
            f"Where is the {word} machine?" if kind in MACHINE_WORDS else f"Where is the {word}?"
        )

    work = [s for s in named if s.room_type in WORK_ZONES] or [
        s for s in named if s.room_type not in BACKGROUND_ROOMS
    ]
    start = next(
        (s for s in named if s.room_type == "lobby"),
        next((s for s in named if s.entrances > 0), None),
    )
    if work:
        target = work[0]
        if start is None or start.space_id == target.space_id:
            start = next((s for s in named if s.space_id != target.space_id), None)
        if start is not None:
            candidates.append(f"Route from {start.name} to {target.name}")
        else:
            candidates.append(f"What is {target.name} used for?")

    if len(stats.floors) > 1:
        floor = max(stats.floors, key=lambda f: f.level)
        candidates.append(f"Which rooms are on {floor.name}?")
    else:
        candidates.append("Which areas are step-free?")

    # Fillers, still with real names.
    if work:
        candidates.append(f"Is {work[0].name} step-free?")
    candidates.append("Where is the nearest lift or staircase?")
    candidates.append("Which areas are step-free?")
    candidates.append("What is this building used for?")
    return clean(candidates)


def run(ctx: StageContext) -> StageReport:
    stats = compute_location_stats(ctx.building())
    suggestions = template_suggestions(stats)
    usage = Usage()
    notes: list[str] = []
    record = ctx.workspace.locations.get(ctx.location_id)
    metadata = (record.metadata if record is not None else None) or LocationMetadata()

    if not ctx.offline:
        assert ctx.client is not None
        facts = {
            "name": stats.name,
            "summary": metadata.summary,
            "floors": [f.name for f in stats.floors],
            "rooms": [
                {"name": s.name, "kind": s.room_type, "floor": s.floor_id}
                for s in sorted(stats.spaces.values(), key=lambda s: -s.area_m2)[:40]
                if s.name.strip()
            ],
            "machines": stats.machines,
            "has_lift": stats.has_lift,
            "has_stairs": stats.has_stairs,
        }
        try:
            result, usage = run_structured(
                ctx.client,
                name="suggester",
                model=ctx.models.summary,
                system=PROMPT,
                user=untrusted(f"Facts:\n{dataset_json(facts, limit=30_000)}"),
                output=Suggestions,
            )
            generated = clean(result.questions)
            if len(generated) == COUNT:
                suggestions = generated
            else:
                notes.append(
                    "Suggestions fell back to the template: model gave too few usable ones."
                )
        except AgentError as exc:
            notes.append(f"Suggestions fell back to the template: {exc}")

    if record is not None:
        metadata.suggestions = suggestions
        record.metadata = metadata
        ctx.workspace.locations.save(record)

    return StageReport(
        stage="suggest",
        status="passed",
        summary=f"{len(suggestions)} chat suggestions written.",
        metrics={"suggestions": len(suggestions)},
        open_issues=notes,
        artifacts=["location.json"],
        usage=usage,
    )

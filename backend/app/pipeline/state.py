"""Shared contracts of the ingestion pipeline: issues, patches, stage reports, runs, reviews.

Big data (building files, meshes, previews) lives in the workspace on disk; graph state
and these records only carry references and compact summaries, so checkpoints and the
orchestrator's context stay small.
"""

import operator
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field

Stage = Literal[
    "reconstruct",
    "raw_validation",
    "graph_build",
    "graph_validate",
    "floorplan",
    "equipment",
    "variant_creator",
    "model_validate",
    "summarize",
    "suggest",
]
STAGE_ORDER: tuple[Stage, ...] = (
    "reconstruct",
    "raw_validation",
    "graph_build",
    "graph_validate",
    "floorplan",
    "equipment",
    "variant_creator",
    "model_validate",
    "summarize",
    "suggest",
)

# "full" builds the whole location (3D model, metadata). "graph" is the benchmark variant:
# deterministic, geometry-preserving cleanup plus the relationship graph, nothing else.
# Both start with "reconstruct", which is skipped when the dataset ships a building.json.
PipelineMode = Literal["full", "graph"]
MODE_STAGES: dict[PipelineMode, tuple[Stage, ...]] = {
    "full": STAGE_ORDER,
    "graph": ("reconstruct", "raw_validation", "graph_build", "graph_validate"),
}


def utcnow() -> datetime:
    return datetime.now(UTC)


# --- raw data validation -------------------------------------------------------------


class Severity(StrEnum):
    ERROR = "error"  # breaks integrity or downstream stages; must be fixed
    WARNING = "warning"  # data quirk; the agent should fix it or justify accepting it
    INFO = "info"


class Issue(BaseModel):
    id: str = Field(description="Stable id: '<issue_class>:<entity ids>'.")
    issue_class: str
    severity: Severity
    entity_ids: list[str]
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    status: Literal["open", "fixed", "accepted"] = "open"
    resolution: str | None = None


class Patch(BaseModel):
    """RFC 6902 operations applied to the cleaned building copy, with provenance."""

    id: str
    ops: list[dict[str, Any]]
    reason: str
    issue_ids: list[str] = Field(default_factory=list)
    issue_class: str | None = None
    author: Literal["auto", "agent"]
    applied_at: datetime = Field(default_factory=utcnow)


# --- stage results and orchestration -------------------------------------------------


class Usage(BaseModel):
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    calls: int = 0

    def add(self, other: "Usage") -> "Usage":
        return Usage(
            model=self.model or other.model,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
            calls=self.calls + other.calls,
        )


class Deviation(BaseModel):
    """A problem found by a validator, attributed to the stage that should fix it."""

    stage: Stage
    kind: str
    entity_id: str | None = None
    message: str


StageStatus = Literal["passed", "failed", "skipped"]


class StageReport(BaseModel):
    stage: Stage
    status: StageStatus
    summary: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    open_issues: list[str] = Field(default_factory=list, description="Short issue messages.")
    deviations: list[Deviation] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list, description="Workspace-relative paths.")
    usage: Usage = Field(default_factory=Usage)
    attempt: int = 1
    finished_at: datetime = Field(default_factory=utcnow)


class Decision(BaseModel):
    action: Literal["proceed", "retry", "ask_human", "abort"]
    target_stage: Stage | None = Field(
        default=None, description="Stage to re-run for 'retry'; defaults to the stage just run."
    )
    reason: str


class ReviewOption(BaseModel):
    id: Literal["accept_with_warnings", "retry", "abort"]
    label: str
    description: str


class ReviewRequest(BaseModel):
    """What the run needs from a human. Shaped so a frontend can render it as a prompt."""

    review_id: str
    run_id: str
    stage: Stage
    title: str
    message: str
    issues: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    options: list[ReviewOption]
    allow_note: bool = True


class ReviewDecision(BaseModel):
    review_id: str
    option: Literal["accept_with_warnings", "retry", "abort"]
    note: str | None = None


DEFAULT_REVIEW_OPTIONS = [
    ReviewOption(
        id="accept_with_warnings",
        label="Accept with warnings",
        description="Keep the current result, record the open problems and continue.",
    ),
    ReviewOption(id="retry", label="Retry stage", description="Run the stage again."),
    ReviewOption(id="abort", label="Abort run", description="Stop; nothing is published."),
]


# --- runs ------------------------------------------------------------------------------


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    NEEDS_REVIEW = "needs_review"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class StageRun(BaseModel):
    status: Literal["pending", "running", "passed", "failed", "skipped"] = "pending"
    attempts: int = 0
    started_at: datetime | None = None
    ended_at: datetime | None = None
    summary: str | None = None


class RunRecord(BaseModel):
    run_id: str
    dataset: str
    location_id: str | None = None
    status: RunStatus = RunStatus.QUEUED
    current_stage: Stage | None = None
    mode: PipelineMode = "full"
    offline: bool = False
    owner_pid: int | None = Field(default=None, description="Process driving the run.")
    owner_boot: str | None = Field(default=None, description="Id of that process's runner.")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    stages: dict[str, StageRun] = Field(
        default_factory=lambda: {str(stage): StageRun() for stage in STAGE_ORDER}
    )
    pending_review: ReviewRequest | None = None
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, Usage] = Field(default_factory=dict, description="Per agent.")
    artifacts: list[str] = Field(default_factory=list)
    error: str | None = None


# --- LangGraph state -------------------------------------------------------------------


def _merge(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {**left, **right}


class PipelineState(TypedDict, total=False):
    run_id: str
    dataset: str
    dataset_dir: str
    location_id: str
    force: bool
    mode: PipelineMode
    # Stage results as StageReport dumps, keyed by stage (last attempt wins).
    reports: Annotated[dict[str, dict[str, Any]], _merge]
    attempts: Annotated[dict[str, int], _merge]
    last_stage: Stage
    decision: dict[str, Any]  # Decision dump from the latest gate
    decisions: Annotated[list[dict[str, Any]], operator.add]
    feedback: Annotated[dict[str, str], _merge]  # stage -> why its last attempt was rejected
    graph_ref: str | None  # filled by the relationship-graph stage once implemented
    skipped: bool  # input unchanged since the last completed run
    reconstruct: bool  # load found no building.json: "reconstruct" runs, then load again
    aborted: bool

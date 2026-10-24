"""Stage 1: find inconsistencies in the raw building and fix them in the cleaned copy.

Deterministic checks find the issues and unambiguous fixes are applied first. The agent
then works through every remaining issue: it patches the data where a correct fix
exists, and accepts an issue only with a specific reason. Every change is a validated
JSON patch with provenance; the raw file is never touched.
"""

import json
from typing import Any, Literal

from pydantic import BaseModel, Field
from shapely.geometry import Polygon

from app.domain.building import BuildingFile
from app.pipeline.agents.base import StageContext, agent_tool, as_json, retry_note, run_subagent
from app.pipeline.state import Issue, Patch, Severity, StageReport, Usage
from app.pipeline.tools.checks import (
    ISSUE_CLASSES,
    inspect_entity,
    run_checks,
    translate_floor_ops,
)
from app.pipeline.tools.patching import apply_patch, deterministic_fixes, id_index

SYSTEM_PROMPT = """\
You are the raw-data validation agent of a building ingestion pipeline. The building
(floors, spaces with polygons, openings, vertical connectors, assets; metres, z up) was
checked by deterministic rules. Your job: resolve every open issue.

- Fix the data whenever a correct fix can be derived from the data itself (geometry,
  references, sibling entities). Use `apply_patch` with RFC 6902 ops addressed by JSON
  pointer (`entity_pointer` gives the pointer of an id), `translate_floor` for per-floor
  misalignment, `trim_overlap` for a space polygon that overlaps another.
- Patches are validated; rejected patches tell you why. Never invent ids or delete
  entities that other entities reference.
- Accept an issue only when it is a faithful property of the source (e.g. an intended
  double-height hall), with a specific reason via `accept_issue`.
- Work class by class, highest severity first. Re-run checks after a batch of fixes.
- When you learn something reusable about this data source, store it with `record_quirk`.
Finish with the structured report once no issue is left open."""


class PatchOp(BaseModel):
    """One RFC 6902 operation. Strict tool schemas need fixed fields, so the value of
    add/replace/test travels as JSON text and `from` (move/copy) as `from_path`."""

    op: Literal["add", "remove", "replace", "move", "copy", "test"]
    path: str = Field(description="JSON pointer, e.g. /spaces/3/polygon")
    value_json: str | None = Field(
        description="JSON-encoded value for add/replace/test, e.g. '90' or '[[0, 0], [1, 0]]'"
    )
    from_path: str | None = Field(description="Source pointer for move/copy")

    def to_rfc6902(self) -> dict[str, Any]:
        op: dict[str, Any] = {"op": self.op, "path": self.path}
        if self.op in ("add", "replace", "test"):
            if self.value_json is None:
                raise ValueError(f"{self.op} at {self.path} needs value_json")
            try:
                op["value"] = json.loads(self.value_json)
            except ValueError as exc:
                raise ValueError(f"value_json at {self.path} is not valid JSON: {exc}") from exc
        if self.op in ("move", "copy"):
            if not self.from_path:
                raise ValueError(f"{self.op} to {self.path} needs from_path")
            op["from"] = self.from_path
        return op


class RawValidationReport(BaseModel):
    summary: str = Field(description="Two or three sentences on what was fixed and accepted.")
    remaining_concerns: list[str] = Field(default_factory=list)


class _Session:
    """Mutable working copy shared by the tools of one agent run."""

    def __init__(self, ctx: StageContext) -> None:
        self.ctx = ctx
        self.store = ctx.workspace.locations
        self.doc = self.store.read_building(ctx.location_id)
        self.issues: dict[str, Issue] = {i.id: i for i in self.store.read_issues(ctx.location_id)}
        self.patch_count = len(self.store.read_patches(ctx.location_id))

    def building(self) -> BuildingFile:
        return BuildingFile.model_validate(self.doc)

    def recheck(self) -> dict[str, Any]:
        """Re-run checks; issues that disappeared are marked fixed, accepted ones stay."""
        current = {i.id: i for i in run_checks(self.building())}
        resolved = []
        for issue_id, issue in self.issues.items():
            if issue_id not in current and issue.status == "open":
                issue.status, issue.resolution = "fixed", issue.resolution or "fixed by patch"
                resolved.append(issue_id)
        for issue_id, issue in current.items():
            known = self.issues.get(issue_id)
            if known is None or known.status != "accepted":
                self.issues[issue_id] = issue  # new, still open (fresh details), or came back
        self.store.write_issues(self.ctx.location_id, list(self.issues.values()))
        return {"resolved": resolved, "open_by_class": self.open_by_class()}

    def open_issues(self) -> list[Issue]:
        return [i for i in self.issues.values() if i.status == "open"]

    def open_by_class(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for issue in self.open_issues():
            counts[issue.issue_class] = counts.get(issue.issue_class, 0) + 1
        return counts

    def patch(
        self,
        ops: list[dict[str, Any]],
        reason: str,
        issue_ids: list[str],
        issue_class: str | None,
        author: str,
    ) -> dict[str, Any]:
        result = apply_patch(self.doc, ops)
        if not result.ok or result.doc is None:
            return {"ok": False, "errors": result.errors}
        self.doc = result.doc
        self.store.write_building(self.ctx.location_id, self.doc)
        self.patch_count += 1
        self.store.append_patch(
            self.ctx.location_id,
            Patch(
                id=f"P{self.patch_count:04d}",
                ops=ops,
                reason=reason,
                issue_ids=issue_ids,
                issue_class=issue_class,
                author="agent" if author == "agent" else "auto",
            ),
        )
        return {"ok": True, "new_issue_ids": result.new_issue_ids}


def _apply_deterministic(session: _Session) -> int:
    applied = 0
    for ops, reason, issue_ids, issue_class in deterministic_fixes(
        session.doc, session.open_issues()
    ):
        if session.patch(ops, reason, issue_ids, issue_class, author="auto")["ok"]:
            applied += 1
    return applied


def _tools(session: _Session) -> list[Any]:
    ctx = session.ctx

    @agent_tool
    def list_issues(issue_class: str | None = None, limit: int = 40) -> str:
        """List open issues, optionally of one class.

        Args:
            issue_class: Only this class (see `issue_classes`); omit for all classes.
            limit: Maximum number of issues to return.
        """
        issues = [i for i in session.open_issues() if issue_class in (None, i.issue_class)]
        return as_json(
            {
                "open_by_class": session.open_by_class(),
                "issues": [i.model_dump(exclude={"status", "resolution"}) for i in issues[:limit]],
                "shown": min(limit, len(issues)),
                "total": len(issues),
            }
        )

    @agent_tool
    def issue_classes() -> str:
        """Descriptions, severities and fix hints of every issue class."""
        return as_json({k: v.__dict__ for k, v in ISSUE_CLASSES.items()})

    @agent_tool
    def inspect(entity_id: str) -> str:
        """Show a floor, space, opening, connector or asset by id with its relations.

        Args:
            entity_id: The entity id.
        """
        try:
            return as_json(inspect_entity(session.building(), entity_id))
        except KeyError:
            return f"Unknown id: {entity_id}"

    @agent_tool
    def entity_pointer(entity_ids: list[str]) -> str:
        """JSON pointers (e.g. /assets/12) of entities, for use in patch ops.

        Args:
            entity_ids: Ids to look up.
        """
        index = id_index(session.doc)
        return as_json({i: index.get(i) for i in entity_ids})

    @agent_tool
    def apply_patch_ops(
        ops: list[PatchOp], reason: str, issue_ids: list[str], issue_class: str
    ) -> str:
        """Apply RFC 6902 operations to the cleaned building. Validated; rejected if invalid.

        Args:
            ops: e.g. [{"op": "replace", "path": "/assets/12/footprint/rotation_deg",
                "value_json": "90", "from_path": null}].
            reason: Why this fix is correct (stored in the patch log).
            issue_ids: Issues this patch resolves.
            issue_class: Class of those issues.
        """
        try:
            raw_ops = [op.to_rfc6902() for op in ops]
        except ValueError as exc:
            return as_json({"ok": False, "errors": [str(exc)]})
        return as_json(session.patch(raw_ops, reason, issue_ids, issue_class, author="agent"))

    @agent_tool
    def translate_floor(
        floor_id: str, dx: float, dy: float, reason: str, issue_ids: list[str]
    ) -> str:
        """Shift every space, opening and asset of one floor by (dx, dy) metres.

        Args:
            floor_id: Floor to move.
            dx: Shift along x in metres.
            dy: Shift along y in metres.
            reason: Why (stored in the patch log).
            issue_ids: Issues this resolves.
        """
        ops = translate_floor_ops(session.doc, floor_id, dx, dy)
        return as_json(session.patch(ops, reason, issue_ids, "floor_misalignment", "agent"))

    @agent_tool
    def trim_overlap(
        trim_space_id: str, keep_space_id: str, reason: str, issue_ids: list[str]
    ) -> str:
        """Cut `keep_space_id`'s polygon out of `trim_space_id`'s polygon.

        Args:
            trim_space_id: Space whose polygon is reduced.
            keep_space_id: Space whose polygon stays as is.
            reason: Why (stored in the patch log).
            issue_ids: Issues this resolves.
        """
        index = id_index(session.doc)
        spaces = {s["id"]: s for s in session.doc["spaces"]}
        if trim_space_id not in spaces or keep_space_id not in spaces:
            return "Unknown space id."
        trimmed = Polygon(spaces[trim_space_id]["polygon"]).difference(
            Polygon(spaces[keep_space_id]["polygon"])
        )
        if trimmed.geom_type != "Polygon" or trimmed.is_empty or trimmed.area < 0.5:
            return f"Cannot trim: result would be {trimmed.geom_type}, area {trimmed.area:.2f}."
        assert isinstance(trimmed, Polygon)
        ring = [[round(x, 4), round(y, 4)] for x, y in list(trimmed.exterior.coords)[:-1]]
        op = {"op": "replace", "path": f"{index[trim_space_id]}/polygon", "value": ring}
        return as_json(session.patch([op], reason, issue_ids, "overlapping_spaces", "agent"))

    @agent_tool
    def accept_issue(issue_ids: list[str], reason: str) -> str:
        """Mark issues as accepted (a faithful property of the source, not an error).

        Args:
            issue_ids: Issues to accept.
            reason: Specific justification.
        """
        done, rejected = [], {}
        for issue_id in issue_ids:
            issue = session.issues.get(issue_id)
            if issue is None or issue.status != "open":
                rejected[issue_id] = "unknown or not open"
            elif issue.severity == Severity.ERROR:
                rejected[issue_id] = "error-severity issues must be fixed, not accepted"
            else:
                issue.status, issue.resolution = "accepted", reason
                done.append(issue_id)
        session.store.write_issues(ctx.location_id, list(session.issues.values()))
        return as_json(
            {"accepted": done, "rejected": rejected, "open_by_class": session.open_by_class()}
        )

    @agent_tool
    def rerun_checks() -> str:
        """Re-run all checks on the cleaned building and update issue statuses."""
        return as_json(session.recheck())

    @agent_tool
    def record_quirk(issue_class: str, lesson: str) -> str:
        """Remember a reusable lesson about this data source for future runs.

        Args:
            issue_class: Issue class the lesson is about.
            lesson: What to expect and how to fix it.
        """
        ctx.workspace.memory.record(
            session.building().building.source, issue_class, lesson, ctx.run_id
        )
        return "stored"

    return [
        list_issues,
        issue_classes,
        inspect,
        entity_pointer,
        apply_patch_ops,
        translate_floor,
        trim_overlap,
        accept_issue,
        rerun_checks,
        record_quirk,
    ]


def run(ctx: StageContext) -> StageReport:
    session = _Session(ctx)
    session.recheck()
    found = len(session.issues)
    auto = _apply_deterministic(session)
    session.recheck()

    usage = Usage()
    summary = f"{found} issues found, {auto} fixed deterministically."
    concerns: list[str] = []
    if not ctx.offline and session.open_issues():
        assert ctx.client is not None
        source = session.building().building.source
        quirks = ctx.workspace.memory.quirks(source)
        user = (
            f"Building {ctx.location_id} (source: {source}). Open issues by class: "
            f"{session.open_by_class()}.\nLessons from earlier runs on this source: "
            f"{quirks or 'none'}.{retry_note(ctx)}"
        )
        report, usage = run_subagent(
            ctx.client,
            name="raw_validation",
            model=ctx.models.worker,
            system=SYSTEM_PROMPT,
            user=user,
            tools=_tools(session),
            output=RawValidationReport,
            max_iterations=60,
        )
        session.recheck()
        summary = f"{summary} {report.summary}"
        concerns = report.remaining_concerns

    statuses = [i.status for i in session.issues.values()]
    still_open = session.open_issues()
    errors = [i for i in still_open if i.severity == Severity.ERROR]
    # Offline nobody can work through the warnings, so only errors block the run.
    blocking = errors if ctx.offline else [i for i in still_open if i.severity != Severity.INFO]
    return StageReport(
        stage="raw_validation",
        status="failed" if blocking else "passed",
        summary=summary,
        metrics={
            "issues_found": found,
            "fixed": statuses.count("fixed"),
            "accepted": statuses.count("accepted"),
            "open": len(still_open),
            "open_errors": len(errors),
            "patches": session.patch_count,
            "open_by_class": session.open_by_class(),
        },
        open_issues=[f"[{i.severity}] {i.message}" for i in still_open[:20]] + concerns,
        artifacts=["building.json", "issues.json", "patches.json"],
        usage=usage,
    )

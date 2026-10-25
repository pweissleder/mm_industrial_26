"""The ingestion pipeline as a LangGraph graph.

    load -> raw_validation -> graph_build -> graph_validate -> floorplan -> equipment
         -> [variant_creator] -> model_validate -> summarize -> suggest -> finalize

Every stage node is followed by the orchestrator `gate`, which routes to the next
stage, back to a stage for a retry, to `review` (a human decision via `interrupt`), or
ends the run. Nodes keep the run's `RunRecord` up to date as they go.

A run in mode "graph" (the benchmark variant) stops after graph_validate and runs
deterministically: no cleanup agent and no orchestrator model, so the cleaned building keeps
the source geometry the benchmark answers are computed on.
"""

import hashlib
import json
from collections.abc import Callable, Hashable
from pathlib import Path
from typing import Any

from anthropic import Anthropic
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from app.domain.building import BuildingFile
from app.pipeline import orchestrator
from app.pipeline.agents import (
    equipment,
    floorplan,
    graph_builder,
    graph_validator,
    model_validator,
    raw_validation,
    suggester,
    summarizer,
    variant_creator,
)
from app.pipeline.agents.base import AgentModels, StageContext
from app.pipeline.state import (
    DEFAULT_REVIEW_OPTIONS,
    MODE_STAGES,
    STAGE_ORDER,
    Decision,
    PipelineMode,
    PipelineState,
    ReviewDecision,
    ReviewRequest,
    RunStatus,
    Stage,
    StageReport,
    utcnow,
)
from app.pipeline.store import LocationRecord, Pillar, Workspace, write_json
from app.storage import BuildingRepository

PipelineGraph = CompiledStateGraph[PipelineState, None, PipelineState, PipelineState]
StageFn = Callable[[StageContext, PipelineState], StageReport]

PILLAR_STAGES: dict[str, list[Stage]] = {
    "validation": ["raw_validation"],
    "graph": ["graph_build", "graph_validate"],
    "assets_3d": ["floorplan", "equipment", "variant_creator", "model_validate"],
    "metadata": ["summarize", "suggest"],
}


def _variant_inputs(state: PipelineState) -> tuple[list[str], dict[str, str]]:
    metrics = state.get("reports", {}).get("equipment", {}).get("metrics", {})
    reasons = {t: "no library model yet" for t in metrics.get("missing_types", [])}
    reasons |= {f["type"]: f["reason"] for f in metrics.get("flagged", [])}
    previous = state.get("reports", {}).get("variant_creator", {})
    created = set(previous.get("metrics", {}).get("created", []))
    return [t for t in metrics.get("to_create", []) if t not in created], reasons


STAGES: dict[Stage, StageFn] = {
    "raw_validation": lambda ctx, s: raw_validation.run(ctx),
    "graph_build": lambda ctx, s: graph_builder.run(ctx),
    "graph_validate": lambda ctx, s: graph_validator.run(ctx),
    "floorplan": lambda ctx, s: floorplan.run(ctx),
    "equipment": lambda ctx, s: equipment.run(ctx),
    "variant_creator": lambda ctx, s: variant_creator.run(ctx, *_variant_inputs(s)),
    "model_validate": lambda ctx, s: model_validator.run(ctx),
    "summarize": lambda ctx, s: summarizer.run(ctx),
    "suggest": lambda ctx, s: suggester.run(ctx),
}


def run_mode(state: PipelineState) -> PipelineMode:
    return state.get("mode", "full")


def next_stage(stage: Stage, state: PipelineState) -> str:
    if stage == "equipment" and not _variant_inputs(state)[0]:
        return "model_validate"
    order = MODE_STAGES[run_mode(state)]
    position = order.index(stage)
    return order[position + 1] if position + 1 < len(order) else "finalize"


def build_pipeline_graph(
    workspace: Workspace,
    client: Anthropic | None,
    models: AgentModels,
    repository: BuildingRepository | None = None,
    max_retries: int = 2,
    checkpointer: BaseCheckpointSaver[str] | None = None,
) -> PipelineGraph:
    runs = workspace.runs
    locations = workspace.locations

    def update_run(run_id: str, **changes: Any) -> None:
        record = runs.get(run_id)
        if record is None:
            return
        stage_changes = changes.pop("stage_changes", None)
        for key, value in changes.items():
            setattr(record, key, value)
        if stage_changes:
            stage, values = stage_changes
            for key, value in values.items():
                setattr(record.stages[stage], key, value)
        runs.save(record)

    def mark_unfinished(location_id: str, note: str) -> None:
        """Pillars a stopped run left half-done: data on disk is mid-rebuild."""
        record = locations.get(location_id)
        if record is None:
            return
        for name, pillar in record.pillars.items():
            if pillar.status in ("pending", "running"):
                record.pillars[name] = Pillar(status="failed", updated_at=utcnow(), note=note)
        locations.save(record)

    def load(state: PipelineState) -> dict[str, Any]:
        dataset_dir = Path(state["dataset_dir"])
        raw_bytes = (dataset_dir / "building.json").read_bytes()
        building = BuildingFile.model_validate_json(raw_bytes)
        location_id = building.building.id
        sha = hashlib.sha256(raw_bytes).hexdigest()

        existing = locations.get(location_id)
        mode = run_mode(state)
        # A full run also leaves a done graph, so a graph run on it has nothing to add.
        last_pillar = "graph" if mode == "graph" else "metadata"
        unchanged = (
            existing is not None
            and existing.raw_sha256 == sha
            and existing.pillars[last_pillar].status == "done"
        )
        update_run(state["run_id"], location_id=location_id, status=RunStatus.RUNNING)
        if unchanged and not state.get("force"):
            if repository is not None:  # the repository may be in-memory and empty again
                cleaned = BuildingFile.model_validate(locations.read_building(location_id))
                repository.save(state["dataset"], cleaned)
            return {"location_id": location_id, "skipped": True}

        record = existing or LocationRecord(
            id=location_id,
            dataset=state["dataset"],
            name=building.building.name,
            source=building.building.source,
            raw_sha256=sha,
        )
        record.raw_sha256 = sha
        record.pillars = {
            p: Pillar()
            if mode == "full" or set(stages) <= set(MODE_STAGES[mode])
            else Pillar(status="skipped", updated_at=utcnow(), note=f"{mode}-only run")
            for p, stages in PILLAR_STAGES.items()
        }
        record.artifacts["source_dir"] = str(dataset_dir)
        record.runs.append(state["run_id"])
        locations.save(record)
        # Each run starts again from the raw data; patches are replayed by the agents.
        raw = json.loads(raw_bytes)
        write_json(locations.path(location_id, "raw.json"), raw)
        locations.write_building(location_id, raw)
        locations.write_issues(location_id, [])
        write_json(locations.path(location_id, "patches.json"), [])
        return {"location_id": location_id, "skipped": False, "reports": {}, "attempts": {}}

    def make_stage_node(stage: Stage) -> Any:
        def node(state: PipelineState) -> dict[str, Any]:
            run_id = state["run_id"]
            attempt = state.get("attempts", {}).get(stage, 0) + 1
            update_run(
                run_id,
                current_stage=stage,
                stage_changes=(
                    stage,
                    {"status": "running", "attempts": attempt, "started_at": utcnow()},
                ),
            )
            ctx = StageContext(
                client=None if run_mode(state) == "graph" else client,
                models=models,
                workspace=workspace,
                run_id=run_id,
                location_id=state["location_id"],
                dataset_dir=Path(state["dataset_dir"]),
                attempt=attempt,
                feedback=state.get("feedback", {}).get(stage),
                preserve_geometry=run_mode(state) == "graph",
            )
            try:
                report = STAGES[stage](ctx, state)
            except Exception as exc:  # a crashing stage is a failed stage, the gate decides
                report = StageReport(
                    stage=stage, status="failed", summary=f"{stage} crashed: {exc!r}"
                )
            report.attempt = attempt

            record = runs.get(run_id)
            if record is not None:
                agent_usage = record.usage.get(stage)
                record.usage[stage] = agent_usage.add(report.usage) if agent_usage else report.usage
                stage_run = record.stages[stage]
                stage_run.status, stage_run.ended_at = report.status, utcnow()
                stage_run.summary = report.summary
                record.artifacts = sorted(set(record.artifacts) | set(report.artifacts))
                runs.save(record)
            reports_dir = locations.path(state["location_id"], "reports")
            write_json(reports_dir / f"{stage}.json", report.model_dump(mode="json"))
            reports = {stage: report.model_dump(mode="json")}
            if stage == "equipment" and not report.metrics.get("to_create"):
                # A variant report from an earlier loop must not count for this model.
                reports["variant_creator"] = StageReport(
                    stage="variant_creator", status="skipped", summary="Nothing to create."
                ).model_dump(mode="json")
            return {
                "reports": reports,
                "attempts": {stage: attempt},
                "last_stage": stage,
                "feedback": {stage: ""},  # consumed; a later re-run starts clean
            }

        return node

    def gate(state: PipelineState) -> dict[str, Any]:
        stage = state["last_stage"]
        report = StageReport.model_validate(state["reports"][stage])
        decision, usage = orchestrator.decide(
            None if run_mode(state) == "graph" else client,
            models.orchestrator,
            report,
            state.get("attempts", {}),
            max_retries,
        )
        record = runs.get(state["run_id"])
        if record is not None:
            previous = record.usage.get("orchestrator")
            record.usage["orchestrator"] = previous.add(usage) if previous else usage
            record.decisions.append({"stage": stage, **decision.model_dump(mode="json")})
            runs.save(record)
        update: dict[str, Any] = {
            "decision": decision.model_dump(mode="json"),
            "decisions": [{"stage": stage, **decision.model_dump(mode="json")}],
        }
        if decision.action == "retry":
            target = decision.target_stage or stage
            problems = [d.message for d in report.deviations if d.stage == target]
            update["feedback"] = {target: "\n".join([decision.reason, *problems[:20]])}
        return update

    def review(state: PipelineState) -> dict[str, Any]:
        stage = state["last_stage"]
        report = StageReport.model_validate(state["reports"][stage])
        reason = state["decision"]["reason"]
        request = ReviewRequest(
            review_id=f"{state['run_id']}:{stage}:{len(state.get('decisions', []))}",
            run_id=state["run_id"],
            stage=stage,
            title=f"{stage.replace('_', ' ').capitalize()} needs a decision",
            message=reason,
            issues=[d.message for d in report.deviations][:30] + report.open_issues[:20],
            artifacts=report.artifacts,
            options=DEFAULT_REVIEW_OPTIONS,
        )
        update_run(state["run_id"], status=RunStatus.NEEDS_REVIEW, pending_review=request)
        answer = ReviewDecision.model_validate(interrupt(request.model_dump(mode="json")))
        update_run(state["run_id"], status=RunStatus.RUNNING, pending_review=None)

        target = orchestrator.retry_target(report)
        decision = {
            "accept_with_warnings": Decision(action="proceed", reason="Accepted by reviewer."),
            "retry": Decision(action="retry", target_stage=target, reason="Retry requested."),
            "abort": Decision(action="abort", reason="Aborted by reviewer."),
        }[answer.option]
        if answer.note:
            decision.reason += f" Note: {answer.note}"
        entry = {
            "stage": stage,
            "review": answer.model_dump(mode="json"),
            **decision.model_dump(mode="json"),
        }
        record = runs.get(state["run_id"])
        if record is not None:
            record.decisions.append(entry)
            runs.save(record)
        update: dict[str, Any] = {
            "decision": decision.model_dump(mode="json"),
            "decisions": [entry],
        }
        if decision.action == "retry":
            problems = [d.message for d in report.deviations if d.stage == target]
            problems += report.open_issues if target == stage else []
            update["feedback"] = {target: "\n".join([decision.reason, *problems[:20]])}
        return update

    def route(state: PipelineState) -> str:
        decision = Decision.model_validate(state["decision"])
        if decision.action == "abort":
            return "abort"
        if decision.action == "ask_human":
            return "review"
        if decision.action == "retry":
            target = decision.target_stage or state["last_stage"]
            if target == "variant_creator" and not _variant_inputs(state)[0]:
                return "equipment"  # nothing to create; rebuild the model instead
            return target
        return next_stage(state["last_stage"], state)

    def abort(state: PipelineState) -> dict[str, Any]:
        mark_unfinished(state["location_id"], "aborted")
        update_run(state["run_id"], status=RunStatus.ABORTED, current_stage=None)
        return {"aborted": True}

    def finalize(state: PipelineState) -> dict[str, Any]:
        location_id = state["location_id"]
        record = locations.get(location_id)
        reports = state.get("reports", {})
        if record is not None:
            mode = run_mode(state)
            for pillar, stages in PILLAR_STAGES.items():
                if not set(stages) <= set(MODE_STAGES[mode]):
                    continue  # marked skipped by load
                statuses = [reports[s]["status"] for s in stages if s in reports]
                status = (
                    "skipped"
                    if not statuses or all(s == "skipped" for s in statuses)
                    else "degraded"
                    if "failed" in statuses
                    else "done"
                )
                record.pillars[pillar] = Pillar(status=status, updated_at=utcnow())
            record.artifacts |= {
                "building": "building.json",
                "issues": "issues.json",
                "patches": "patches.json",
            }
            if mode == "full":
                record.artifacts |= {"model": "3d/model.glb", "model_index": "3d/model_index.json"}
            locations.save(record)
        if repository is not None:
            cleaned = BuildingFile.model_validate(locations.read_building(location_id))
            repository.save(state["dataset"], cleaned)
        update_run(state["run_id"], status=RunStatus.COMPLETED, current_stage=None)
        return {}

    builder = StateGraph(PipelineState)
    builder.add_node("load", load)
    for stage in STAGE_ORDER:
        builder.add_node(stage, make_stage_node(stage))
        builder.add_edge(stage, "gate")
    builder.add_node("gate", gate)
    builder.add_node("review", review)
    builder.add_node("abort", abort)
    builder.add_node("finalize", finalize)

    targets: dict[Hashable, str] = {
        name: name for name in (*STAGE_ORDER, "review", "abort", "finalize")
    }
    builder.add_edge(START, "load")
    builder.add_conditional_edges(
        "load", lambda s: END if s.get("skipped") else "raw_validation", [END, "raw_validation"]
    )
    builder.add_conditional_edges("gate", route, targets)
    builder.add_conditional_edges("review", route, targets)
    builder.add_edge("abort", END)
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer or InMemorySaver())

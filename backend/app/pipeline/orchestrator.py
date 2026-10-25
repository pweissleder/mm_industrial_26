"""The orchestration agent: decides after each stage whether the run proceeds.

It sees only the compact stage report and the attempt counters, never raw data. The
stage order is fixed by the graph; the orchestrator chooses between proceed, retry (of
the stage that must fix the problem), ask_human and abort, within guardrails: a failed
stage never just proceeds, and retries are capped.
"""

from anthropic import Anthropic

from app.pipeline.agents.base import AgentError, dataset_json, run_structured
from app.pipeline.state import Decision, Stage, StageReport, Usage

# Gates where the orchestrator is consulted even when the stage passed.
CHECKPOINTS: set[Stage] = {
    "reconstruct",
    "raw_validation",
    "graph_validate",
    "model_validate",
    "summarize",
}
# Stages a failure of a stage may be sent back to.
RETRY_TARGETS: dict[Stage, list[Stage]] = {
    "reconstruct": ["reconstruct"],
    "model_validate": ["floorplan", "equipment", "variant_creator", "model_validate"],
    "graph_validate": ["graph_build", "graph_validate"],
}

SYSTEM_PROMPT = """\
You orchestrate a building ingestion pipeline with fixed stages: reconstruct ->
raw_validation -> graph_build -> graph_validate -> floorplan -> equipment ->
variant_creator -> model_validate -> summarize -> suggest. reconstruct rebuilds
building.json from floor plan SVGs/images + equipment.csv when the dataset has none; it is
skipped when building.json is provided. After a stage you get its report and decide:
- proceed: the result is good enough for the next stage (open info-level notes are fine).
- retry: re-run a stage (target_stage) when the report shows a fixable problem; target
  the stage the deviations are attributed to.
- ask_human: the problem needs a judgment call, or retries are exhausted.
- abort: the input is unusable.
Be decisive and brief; prefer proceed for passed stages."""


def retry_target(report: StageReport) -> Stage:
    allowed = RETRY_TARGETS.get(report.stage, [report.stage])
    blamed = [d.stage for d in report.deviations if d.stage in allowed]
    return min(blamed, key=allowed.index) if blamed else report.stage


def default_decision(report: StageReport, attempts: dict[str, int], max_retries: int) -> Decision:
    if report.status != "failed":
        return Decision(action="proceed", reason=f"{report.stage} {report.status}.")
    target = retry_target(report)
    if attempts.get(target, 0) <= max_retries:
        return Decision(action="retry", target_stage=target, reason=report.summary)
    return Decision(action="ask_human", reason=f"{report.stage} still failing: {report.summary}")


def _guard(
    decision: Decision, report: StageReport, attempts: dict[str, int], max_retries: int
) -> Decision:
    allowed = RETRY_TARGETS.get(report.stage, [report.stage])
    if decision.action == "proceed" and report.status == "failed":
        return default_decision(report, attempts, max_retries)
    if decision.action == "retry":
        target = decision.target_stage or report.stage
        if target not in allowed:
            target = retry_target(report)
        if attempts.get(target, 0) > max_retries:
            return Decision(action="ask_human", reason=f"Retries exhausted: {decision.reason}")
        return decision.model_copy(update={"target_stage": target})
    return decision


def decide(
    client: Anthropic | None,
    model: str,
    report: StageReport,
    attempts: dict[str, int],
    max_retries: int,
    offline_review_on_failure: bool = True,
) -> tuple[Decision, Usage]:
    """Gate decision for `report`; deterministic offline and for routine passes."""
    if report.metrics.get("suspicious_text"):
        # Decided here, not by a model: the report quotes the very data that is in question.
        return Decision(
            action="ask_human",
            reason=(
                "Possible prompt injection in the uploaded data: text fields look like "
                "instructions or scripts. They were replaced with neutral values where "
                "possible; review them before the run continues."
            ),
        ), Usage()
    if client is None:
        if report.status == "failed" and offline_review_on_failure:
            # Deterministic stages give the same result again, so a retry can't help.
            return Decision(action="ask_human", reason=report.summary), Usage()
        return default_decision(report, attempts, max_retries), Usage()
    if report.status != "failed" and report.stage not in CHECKPOINTS:
        return default_decision(report, attempts, max_retries), Usage()

    compact = report.model_dump(mode="json", exclude={"usage", "finished_at"})
    user = (
        f"Stage report:\n{dataset_json(compact, limit=12_000)}\n"
        f"Attempts so far: {attempts}. Max retries per stage: {max_retries}. "
        f"Allowed retry targets: {RETRY_TARGETS.get(report.stage, [report.stage])}."
    )
    try:
        decision, usage = run_structured(
            client,
            name="orchestrator",
            model=model,
            system=SYSTEM_PROMPT,
            user=user,
            output=Decision,
            effort="low",
        )
    except AgentError as exc:
        return default_decision(report, attempts, max_retries).model_copy(
            update={"reason": f"orchestrator unavailable ({exc}); default policy"}
        ), Usage()
    return _guard(decision, report, attempts, max_retries), usage

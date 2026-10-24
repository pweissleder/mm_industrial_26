"""Shared harness for the pipeline's specialised subagents.

Each subagent is one Claude API Tool Runner loop: a fresh, stage-scoped context, only
the tools of its stage, and a structured final report (`output_format`). Nothing but that
report flows back to the orchestrator; full transcripts go to LangSmith.

Without a client (no API key) every stage runs its deterministic path instead, so the
pipeline works offline end to end, like the chat agent's offline mode.
"""

import base64
import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeVar, cast

from anthropic import Anthropic, APIError, beta_tool, transform_schema
from anthropic.lib.tools import BetaFunctionTool
from langsmith import traceable
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.domain.building import BuildingFile
from app.pipeline.state import Usage
from app.pipeline.store import Workspace

T = TypeVar("T", bound=BaseModel)
Effort = Literal["low", "medium", "high", "xhigh", "max"]

# Route refused requests to a fallback model server-side (Opus/Sonnet only; Haiku has none).
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOOL_RESULT_CHARS = 20_000


class AgentError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentModels:
    orchestrator: str
    validator: str
    worker: str
    summary: str

    @classmethod
    def from_settings(cls, settings: Settings) -> "AgentModels":
        return cls(
            orchestrator=settings.pipeline_orchestrator_model,
            validator=settings.pipeline_validator_model,
            worker=settings.pipeline_worker_model,
            summary=settings.pipeline_summary_model,
        )


@dataclass
class StageContext:
    """Everything a stage needs; `client is None` means offline (deterministic only)."""

    client: Anthropic | None
    models: AgentModels
    workspace: Workspace
    run_id: str
    location_id: str
    dataset_dir: Path
    attempt: int = 1
    feedback: str | None = None  # deviations / orchestrator reason when this is a retry

    @property
    def offline(self) -> bool:
        return self.client is None

    def path(self, name: str) -> Path:
        return self.workspace.locations.path(self.location_id, name)

    def building(self) -> BuildingFile:
        return BuildingFile.model_validate(self.workspace.locations.read_building(self.location_id))


def agent_tool(func: Callable[..., Any]) -> BetaFunctionTool[Any]:
    """`beta_tool` in strict mode, for every pipeline agent tool.

    With `strict: true` the API makes Claude's arguments match the schema exactly (names,
    types, required fields), so a misnamed argument can't cost a turn. Constraints strict
    mode can't express (bounds, lengths) are moved into the field descriptions by
    `transform_schema`; pydantic still enforces them when the tool is called.
    """
    tool = beta_tool(func, strict=True)
    tool.input_schema = cast(Any, transform_schema(cast(dict[str, Any], tool.input_schema)))
    return tool


def as_json(data: Any, limit: int = MAX_TOOL_RESULT_CHARS) -> str:
    """Tool result text; truncated with a note so one call can't flood the context."""
    text = json.dumps(data, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [truncated {len(text) - limit} chars; narrow the request]"


def image_block(path: Path, caption: str) -> list[Any]:
    data = base64.standard_b64encode(path.read_bytes()).decode()
    return [
        {"type": "text", "text": caption},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}},
    ]


def _usage_of(message: Any, model: str) -> Usage:
    usage = getattr(message, "usage", None)
    if usage is None:
        return Usage(model=model)
    return Usage(
        model=model,
        input_tokens=usage.input_tokens or 0,
        output_tokens=usage.output_tokens or 0,
        cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        calls=1,
    )


def run_subagent(
    client: Anthropic,
    *,
    name: str,
    model: str,
    system: str,
    user: str | Sequence[dict[str, Any]],
    tools: Iterable[BetaFunctionTool[Any]],
    output: type[T],
    max_iterations: int = 40,
    effort: Effort = "medium",
) -> tuple[T, Usage]:
    """Run one Tool Runner loop until the agent returns its structured report."""
    result = _run_subagent(
        client,
        name=name,
        model=model,
        system=system,
        user=user,
        tools=tools,
        output=output,
        max_iterations=max_iterations,
        effort=effort,
    )
    return cast(tuple[T, Usage], result)


@traceable(run_type="chain", name="subagent", process_inputs=lambda i: {"name": i["name"]})
def _run_subagent(
    client: Anthropic,
    *,
    name: str,
    model: str,
    system: str,
    user: str | Sequence[dict[str, Any]],
    tools: Iterable[BetaFunctionTool[Any]],
    output: type[BaseModel],
    max_iterations: int,
    effort: Effort,
) -> tuple[BaseModel, Usage]:
    extra: dict[str, Any] = {}
    if "haiku" not in model:
        extra = {"fallbacks": "default", "betas": [FALLBACK_BETA]}
    runner = client.beta.messages.tool_runner(  # type: ignore[call-overload]
        model=model,
        max_tokens=16_000,
        system=system,
        tools=list(tools),
        messages=[{"role": "user", "content": user}],
        output_format=output,
        output_config={"effort": effort},
        max_iterations=max_iterations,
        **extra,
    )
    usage = Usage(model=model)
    last = None
    try:
        for message in runner:
            last = message
            usage = usage.add(_usage_of(message, model))
    except (APIError, ValidationError) as exc:
        raise AgentError(f"{name}: {exc!r}") from exc
    if last is None:
        raise AgentError(f"{name}: no response")
    if last.stop_reason == "refusal":
        raise AgentError(f"{name}: request refused ({last.stop_details})")
    parsed = last.parsed_output
    if parsed is None:
        raise AgentError(
            f"{name}: finished without a structured report (stop_reason={last.stop_reason}, "
            f"possibly hit max_iterations={max_iterations})"
        )
    return parsed, usage


def run_structured(
    client: Anthropic,
    *,
    name: str,
    model: str,
    system: str,
    user: str | Sequence[dict[str, Any]],
    output: type[T],
    effort: Effort = "low",
) -> tuple[T, Usage]:
    """One tool-less call with a structured answer (gates, summaries)."""
    result = _run_structured(
        client, name=name, model=model, system=system, user=user, output=output, effort=effort
    )
    return cast(tuple[T, Usage], result)


@traceable(run_type="llm", name="structured_call", process_inputs=lambda i: {"name": i["name"]})
def _run_structured(
    client: Anthropic,
    *,
    name: str,
    model: str,
    system: str,
    user: str | Sequence[dict[str, Any]],
    output: type[BaseModel],
    effort: Effort,
) -> tuple[BaseModel, Usage]:
    extra: dict[str, Any] = {}
    if "haiku" not in model:
        extra = {"fallbacks": "default", "betas": [FALLBACK_BETA]}
    try:
        message = _parse(client, model, system, user, output, effort, extra)
    except (APIError, ValidationError) as exc:
        raise AgentError(f"{name}: {exc!r}") from exc
    if message.stop_reason == "refusal":
        raise AgentError(f"{name}: request refused ({message.stop_details})")
    if message.parsed_output is None:
        raise AgentError(f"{name}: no structured answer (stop_reason={message.stop_reason})")
    return message.parsed_output, _usage_of(message, model)


def _parse(
    client: Anthropic,
    model: str,
    system: str,
    user: str | Sequence[dict[str, Any]],
    output: type[BaseModel],
    effort: Effort,
    extra: dict[str, Any],
) -> Any:
    return client.beta.messages.parse(
        model=model,
        max_tokens=16_000,
        system=system,
        messages=[{"role": "user", "content": user}],  # type: ignore[typeddict-item]
        output_format=output,
        output_config={"effort": effort},
        **extra,
    )


def retry_note(ctx: StageContext) -> str:
    if ctx.attempt <= 1 or not ctx.feedback:
        return ""
    return (
        f"\n\nThis is attempt {ctx.attempt}. The previous attempt was rejected because:\n"
        f"{ctx.feedback}\nFix exactly these problems."
    )

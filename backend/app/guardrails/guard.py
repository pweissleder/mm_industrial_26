"""Input guard: rules, then the optional classifier, as the first node of a chat graph."""

import logging
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, MessagesState, StateGraph
from langsmith import get_current_run_tree, traceable

from app.guardrails.classifier import InputClassifier
from app.guardrails.rules import check_rules, normalize
from app.guardrails.verdict import ALLOWED, Category, Verdict

logger = logging.getLogger("app.guardrails")

GUARDRAIL_KEY = "guardrail"
LOG_PREVIEW_CHARS = 200

REFUSALS: dict[Category, str] = {
    "off_topic": (
        "I can only help with this building and the AriNav app: rooms, routes, floors, "
        "equipment, safety and access. What would you like to know?"
    ),
    "prompt_injection": (
        "I can't help with that request. Ask me about rooms, routes or equipment in this building."
    ),
    "unsafe": (
        "I can't help with that request. Ask me about rooms, routes or equipment in this building."
    ),
}


class InputGuard:
    """Checks the latest user message; `classifier=None` means rules only (offline)."""

    def __init__(self, classifier: InputClassifier | None = None) -> None:
        self._classifier = classifier

    @traceable(run_type="chain", name="input_guardrail")
    async def check(self, messages: Sequence[BaseMessage]) -> Verdict:
        *earlier, last = messages
        text = normalize(last.text)
        verdict = check_rules(text)
        if verdict is None and self._classifier is not None:
            history = [
                ("user" if isinstance(m, HumanMessage) else "assistant", m.text) for m in earlier
            ]
            verdict = await self._classifier.classify(text, history)
        verdict = verdict or ALLOWED

        run = get_current_run_tree()
        if run is not None:
            run.add_tags([f"guardrail:{verdict.category}"])
            run.add_metadata({GUARDRAIL_KEY: verdict.model_dump()})
        return verdict


def guardrail_of(message: BaseMessage) -> dict[str, str] | None:
    """The verdict attached to a refusal produced by the guard, if any."""
    info: dict[str, str] | None = message.additional_kwargs.get(GUARDRAIL_KEY)
    return info


def add_input_guard(
    builder: StateGraph[Any, Any, Any, Any], guard: InputGuard, next_node: str
) -> None:
    """Put a `guard` node at START: blocked turns get a refusal and end, others go on."""

    async def guard_node(state: MessagesState, config: RunnableConfig) -> dict[str, Any]:
        messages = state["messages"]
        verdict = await guard.check(messages)
        thread_id = config.get("configurable", {}).get("thread_id")
        if not verdict.blocked:
            logger.debug("[GUARDRAIL ALLOWED] source=%s thread=%s", verdict.source, thread_id)
            return {}

        logger.warning(
            '[GUARDRAIL BLOCKED] category=%s source=%s thread=%s reason="%s" message=%r',
            verdict.category,
            verdict.source,
            thread_id,
            verdict.reason,
            messages[-1].text[:LOG_PREVIEW_CHARS],
        )
        refusal = AIMessage(
            content=REFUSALS[verdict.category],
            additional_kwargs={GUARDRAIL_KEY: verdict.model_dump()},
        )
        return {"messages": [refusal]}

    def route(state: MessagesState) -> str:
        return END if guardrail_of(state["messages"][-1]) else next_node

    builder.add_node("guard", guard_node)
    builder.add_edge(START, "guard")
    builder.add_conditional_edges("guard", route, [next_node, END])

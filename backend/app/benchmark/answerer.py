"""Answers benchmark questions with the app's chat agent (same graph, primer and navgraph
tools as /api/chat), a cheaper model and a strict answer format.

Every question runs in its own thread on a fresh navigator, so an edit one question makes
(a closure, a new door) never leaks into another.

The navigator's primer is frozen for the whole question. A closure set mid-question would
otherwise add a "Currently closed" line to the system prompt, and the API rejects the
model's earlier thinking blocks once the system prompt they were made with has changed.
The tool results already tell the model what it closed.

Route answers then pass the deterministic `AnswerNormaliser` (exact space ids). A question
that hits the rate limit or an overloaded API is retried with backoff, in a fresh thread.
"""

import asyncio
import random
from collections import Counter
from dataclasses import dataclass
from typing import Any

import anthropic
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage, HumanMessage
from pydantic import SecretStr

from app.agent import AgentContext, build_graph
from app.benchmark.normalise import AnswerNormaliser, route_results
from app.domain.building import BuildingFile
from app.navigation.navgraph_engine import NavgraphNavigator

DEFAULT_MODEL = "claude-haiku-5-5"
TIMEOUT_S = 600
RECURSION_LIMIT = 80
RETRIES = 6
RETRYABLE = (anthropic.RateLimitError, anthropic.InternalServerError)

# USD per million tokens: input, output, cache read, cache write.
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
}

INSTRUCTIONS = """\
Benchmark mode. Nobody will answer follow-up questions, so never ask back. Every number comes
from a tool; never estimate.

Names: rooms often share a name ("Büro", "Generic room", "WC", "Lift 1" on every floor).
Resolve a name with the floor the question gives ("Office 0.30 (Floor 1)" means floor 1); if
a tool still returns `ambiguous`, take the candidate on that floor whose name or number
matches exactly, say which one you used, and answer.

The answer format:
1. If the question states one (a line such as "Finish with a line 'Answer: <room name or
   number>, <area> m²'", named fields, or a JSON shape), reproduce it exactly, filled in:
   same order, same separators, same units, nothing added. A requested JSON object goes on
   the Answer line as one valid JSON object with exactly the requested keys.
2. Otherwise end with exactly one line that starts with `Answer:`:
- Numbers (areas, distances, counts): exactly one number on the line, no ranges, no second
  number (write "Answer: 42.5", not "Answer: 42.5 m over 3 floors").
- A room, or where something is: the room's name with its number or ref as the building
  writes it ("Answer: Office 0.30", "Answer: Büro 1.02"), never a bare shared name; when the
  room has no number and its name is not unique, add its space id in brackets. For the
  largest room also its area: "Answer: Office 0.30, 23.4 m²".
- Any question about getting from one place to another (how do I get, is there a route,
  directions, tours), unless it asks for a number: the ordered list of spaces you pass
  through, separated by " -> ", as full space ids copied character for character from the
  `spaces` of the `route` tool result, never shortened: "Answer: <id> -> <id> -> <id>".
  Use the profile the question implies (step_free for no stairs / wheelchair / trolley,
  forklift for forklifts) and `avoid` any closed element. A route to a machine or item ends
  in the room that holds it.
- If no route exists under the rules, write "Answer: no route" and say which rule blocks it.
The Answer line is always the last line of the reply."""


class _FrozenPrimer:
    """A navigator whose primer stays as it was when the question started."""

    def __init__(self, navigator: NavgraphNavigator) -> None:
        self._navigator = navigator
        self._primer = navigator.primer()

    def primer(self) -> str:
        return self._primer

    def tool_schemas(self) -> list[dict[str, Any]]:
        return self._navigator.tool_schemas()

    def call(self, name: str, args: dict[str, Any]) -> str:
        return self._navigator.call(name, args)

    @property
    def version(self) -> int:
        return self._navigator.version

    def snapshot(self) -> dict[str, Any]:
        return self._navigator.snapshot()


def _text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content if isinstance(part, dict))


@dataclass(frozen=True)
class Reply:
    text: str  # what is submitted
    raw: str  # the model's own reply, before the answer line was normalised


class Answerer:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        self.model = model
        chat = ChatAnthropic(model=model, api_key=SecretStr(api_key), max_retries=4)
        self.graph = build_graph(chat, instructions=INSTRUCTIONS)
        self.usage: Counter[str] = Counter()
        self.retries = 0
        self._normalisers: dict[str, AnswerNormaliser] = {}

    async def ask(
        self, building: BuildingFile, thread_id: str, question: str, kind: str = ""
    ) -> Reply:
        for attempt in range(RETRIES + 1):
            try:
                state = await self._run(building, f"{thread_id}#{attempt}", question)
                break
            except RETRYABLE:
                if attempt == RETRIES:
                    raise
                self.retries += 1
                await asyncio.sleep(min(120.0, 10.0 * 2**attempt) * (0.5 + random.random()))
        messages = state["messages"]
        raw = _text(messages[-1])
        key = building.building.id
        if key not in self._normalisers:
            self._normalisers[key] = AnswerNormaliser(building)
        text = self._normalisers[key].normalise(raw, kind, route_results(messages))
        return Reply(text=text, raw=raw)

    async def _run(self, building: BuildingFile, thread_id: str, question: str) -> dict[str, Any]:
        navigator = _FrozenPrimer(NavgraphNavigator(building))
        state: dict[str, Any] = await asyncio.wait_for(
            self.graph.ainvoke(
                {"messages": [HumanMessage(question)]},
                {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT},
                context=AgentContext(navigator=navigator),
            ),
            TIMEOUT_S,
        )
        for message in state["messages"]:
            usage = getattr(message, "usage_metadata", None) or {}
            details = usage.get("input_token_details") or {}
            read, write = details.get("cache_read", 0), details.get("cache_creation", 0)
            # langchain's input_tokens includes cache reads and writes; split them for pricing
            self.usage["input_tokens"] += usage.get("input_tokens", 0) - read - write
            self.usage["cache_read_input_tokens"] += read
            self.usage["cache_creation_input_tokens"] += write
            self.usage["output_tokens"] += usage.get("output_tokens", 0)
        return state

    def cost(self) -> float | None:
        prices = PRICES.get(self.model)
        if prices is None:
            return None
        keys = ("input_tokens", "output_tokens", "cache_read_input_tokens")
        tokens = [self.usage[k] for k in keys] + [self.usage["cache_creation_input_tokens"]]
        return sum(n * p for n, p in zip(tokens, prices, strict=True)) / 1_000_000

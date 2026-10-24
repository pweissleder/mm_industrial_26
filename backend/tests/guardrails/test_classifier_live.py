"""Runs the real classifier over the benchmark and related questions (`poe test-live`)."""

import asyncio

import pytest
from anthropic import AsyncAnthropic

from app.config import get_settings
from app.guardrails import AnthropicInputClassifier, Verdict
from tests.guardrails.cases import OFF_TOPIC, RELATED, SUBTLE_ATTACKS, benchmark_fixture

pytestmark = pytest.mark.live


async def classify_all(questions: list[str]) -> list[tuple[str, Verdict]]:
    settings = get_settings()
    if not settings.anthropic_api_key:
        pytest.skip("ANTHROPIC_API_KEY not set")
    classifier = AnthropicInputClassifier(
        AsyncAnthropic(api_key=settings.anthropic_api_key), settings.guardrail_model
    )
    limit = asyncio.Semaphore(8)

    async def one(question: str) -> tuple[str, Verdict]:
        async with limit:
            return question, await classifier.classify(question, [])

    return await asyncio.gather(*(one(q) for q in questions))


async def test_benchmark_and_related_questions_are_allowed() -> None:
    results = await classify_all(benchmark_fixture() + RELATED)

    rejected = [(q, v.category, v.reason) for q, v in results if v.blocked]
    for item in rejected:
        print(item)
    assert rejected == []


async def test_off_topic_and_subtle_attacks_are_blocked() -> None:
    results = await classify_all(OFF_TOPIC + SUBTLE_ATTACKS)

    allowed = [(q, v.reason) for q, v in results if not v.blocked]
    assert allowed == []

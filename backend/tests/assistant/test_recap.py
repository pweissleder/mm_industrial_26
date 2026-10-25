"""OFFLINE Call Wrapped: facts from the memory, Claude's words validated with one retry and a
fallback, the every-N-conversations trigger and the /api/recaps endpoints."""

import json
from pathlib import Path
from typing import Any

import pytest

from app.assistant.memory import AgentMemory
from app.assistant.recap import RecapService, build_facts
from app.assistant.service import AssistantError
from app.config import Settings
from tests.assistant.test_assistant_integration import (
    OFFICE_F1,
    B,
    datasets_dir,  # noqa: F401  (fixture)
    ingest,  # noqa: F401  (fixture)
    make_client,
)
from tests.assistant.test_assistant_unit import update

GOOD: dict[str, Any] = {
    "topTopics": ["Nearest toilet", "Coffee break", "Taking the stairs"],
    "mood": "Curious",
    "moodEmoji": "🔎",
    "bestQuote": "where can I get a coffee?",
    "summary": "You found your way around the factory. Coffee and toilets were top of mind.",
    "nextSteps": ["Try a step-free route next time."],
}


class FakeClaude:
    """Replays answers (dict = JSON text, str = raw text, Exception = raised); records calls."""

    def __init__(self, *answers: Any) -> None:
        self.answers, self.requests = list(answers), []

    def create(self, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(kwargs)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        text = json.dumps(answer, ensure_ascii=False) if isinstance(answer, dict) else answer
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}


def seeded() -> AgentMemory:
    memory = AgentMemory()
    memory.add_message("s1", "b1", "user", "Where is the nearest toilet?", voice=True)
    memory.add_search("s1", "b1", "find_nearest", "room_type:toilet", 2)
    memory.add_search("s1", "b1", "find_places", "Office 1.04", 1)
    route = {
        "mode": "official",
        "reachable": True,
        "start": {"kind": "space", "id": "S1"},
        "target": {"kind": "space", "id": "S2"},
        "from": "Office 1.04",
        "to": "Toilet 0.22",
        "walk_m": 27.9,
        "length_m": 34.9,
        "floor_changes": [{"kind": "stairs", "delta_m": -3.5}],
    }
    memory.add_route("s1", "b1", route)
    memory.add_message("s1", "b1", "assistant", "Toilet 0.22 is downstairs, about 35 m.")
    memory.add_message("s2", "b1", "user", "Hi, where can I get a coffee?")
    memory.add_search("s2", "b1", "find_nearest", "asset_type:coffee_machine", 1)
    memory.add_route("s2", "b1", route | {"floor_changes": [{"kind": "lift", "delta_m": 7.2}]})
    memory.add_message("s2", "b1", "assistant", "The coffee machine is two floors up.")
    return memory


def service(memory: AgentMemory, transport: Any, every: int = 2) -> RecapService:
    return RecapService(
        memory, transport, every=every, building_names=lambda: {"b1": "Factory"}, background=False
    )


def ready(memory: AgentMemory, transport: Any) -> dict[str, Any]:
    stub = service(memory, transport).start(["s1", "s2"])
    recap = memory.recap(stub["id"])
    assert recap is not None and recap["status"] == "ready"
    return recap


def test_facts_come_from_the_memory_only() -> None:
    memory = seeded()
    messages, events = memory.conversation(["s1", "s2"])
    facts = build_facts(messages, events, memory.stats(), {"b1": "Factory"})
    assert facts["conversations"] == 2 and facts["buildings"] == ["Factory"]
    assert facts["talk"]["questions"] == 2 and facts["talk"]["voice_questions"] == 1
    # Name lookups (find_places) stay out of "what you looked for".
    assert [s["category"] for s in facts["searches"]] == ["toilet", "coffee_machine"]
    assert facts["routes"]["count"] == 2 and facts["routes"]["distance_m"] == 55.8
    assert facts["routes"]["longest"]["to"] == "Toilet 0.22"
    assert facts["routes"]["comparison"] == {"unit": "pool lengths", "value": 2.2}
    assert facts["vertical"]["floor_changes"] == {"stairs": 1, "lift": 1}
    assert facts["vertical"]["climbed_m"]["lift"] == 7.2
    assert facts["vertical"]["steps"] == 21  # 3.5 m of stairs / 0.17 m; the lift doesn't count
    assert facts["alltime"]["conversations"] == 2
    assert [s["category"] for s in facts["alltime"]["top_searches"]] == ["toilet", "coffee_machine"]


def test_valid_answer_is_used_and_numbers_stay_out_of_claude() -> None:
    claude = FakeClaude(GOOD)
    recap = ready(seeded(), claude)
    assert recap["source"] == "claude" and recap["content"]["topTopics"] == GOOD["topTopics"]
    assert recap["content"]["bestQuote"] == "where can I get a coffee?"  # verbatim: kept
    request = claude.requests[0]
    assert request["model"] == "claude-sonnet-5-5"
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert "Where is the nearest toilet?" in request["messages"][0]["content"]


def test_invented_quote_is_dropped() -> None:
    recap = ready(seeded(), FakeClaude(GOOD | {"bestQuote": "Best building ever!"}))
    assert recap["source"] == "claude" and recap["content"]["bestQuote"] is None


def test_invalid_answer_is_retried_once() -> None:
    claude = FakeClaude("not json", GOOD)
    recap = ready(seeded(), claude)
    assert recap["source"] == "claude" and len(claude.requests) == 2


@pytest.mark.parametrize(
    "answers",
    [
        ("{broken", "still not json"),
        (GOOD | {"topTopics": ["Only one"]}, GOOD | {"moodEmoji": "happy"}),
        (GOOD | {"extra": 1}, AssistantError("Claude is busy")),
    ],
    ids=["invalid-json", "schema-violation", "extra-field-then-api-error"],
)
def test_invalid_answer_twice_falls_back_to_the_facts(answers: tuple[Any, ...]) -> None:
    claude = FakeClaude(*answers)
    recap = ready(seeded(), claude)
    assert len(claude.requests) == 2
    content = recap["content"]
    assert recap["source"] == "fallback" and len(content["topTopics"]) == 3
    assert content["topTopics"][:2] == ["Toilet", "Coffee machine"]
    assert content["mood"] and content["moodEmoji"] and content["bestQuote"] is None
    assert "2 conversations" in content["summary"] and "55.8 m" in content["summary"]
    assert recap["facts"]["routes"]["count"] == 2  # the numbers are unaffected


def test_offline_recap_uses_the_fallback() -> None:
    recap = ready(seeded(), None)
    assert recap["source"] == "fallback"


def test_recap_starts_after_every_n_ended_conversations() -> None:
    memory = AgentMemory()
    recaps = service(memory, None, every=2)
    memory.add_message("a", "b1", "user", "hello")
    memory.add_message("b", "b1", "user", "hi")  # starting b ends a
    assert recaps.after_end() is None  # one ended, two needed
    memory.end_session("b")  # "New conversation"
    stub = recaps.after_end()
    assert stub is not None and memory.recap(stub["id"])["session_ids"] == ["a", "b"]
    assert recaps.after_end() is None  # these two are recapped now
    memory.add_message("b", "b1", "user", "late message")  # an ended one stays ended
    assert memory.open == {}


def test_recap_api_end_to_end(
    settings: Settings,
    datasets_dir: Path,  # noqa: F811
    ingest: Any,  # noqa: F811
) -> None:
    settings.recap_every_sessions = 2
    script = [
        update(start={"kind": "space", "id": OFFICE_F1}),
        ("find_nearest", {"category": "room_type:toilet", "step_free": None, "and_route": True}),
        "The nearest toilet is downstairs.",
        "Hello again!",
    ]
    with make_client(settings, datasets_dir, script) as client:
        ingest(client)
        recaps = client.app.state.recaps  # type: ignore[attr-defined]
        recaps.background, recaps.transport = False, FakeClaude("{broken", "{broken")
        assert client.post("/api/recaps").status_code == 409  # nothing to recap yet
        body = {"text": "I'm in Office 1.04, nearest toilet?", "ui": {"building_id": B}}
        first = client.post("/api/assistant/turn", json={"session_id": "conv-0001"} | body)
        assert first.json()["recap"] is None
        second = client.post(
            "/api/assistant/turn",
            json={"session_id": "conv-0002", "text": "hi", "ui": {"building_id": B}},
        )
        assert second.json()["recap"] is None  # conv-0001 ended; one of two
        reset = client.post("/api/assistant/reset", json={"session_id": "conv-0002"}).json()
        stub = reset["recap"]
        recap = client.get(f"/api/recaps/{stub['id']}").json()
        assert client.get("/api/recaps/latest").json()["id"] == stub["id"]
    assert recap["status"] == "ready" and recap["source"] == "fallback"
    assert recap["facts"]["searches"][0]["category"] == "toilet"
    assert recap["facts"]["vertical"]["floor_changes"]["stairs"] >= 1
    assert recap["facts"]["buildings"] and recap["facts"]["talk"]["questions"] == 2

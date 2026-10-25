"""OFFLINE: POST /api/assistant/speak with a fake voice on app.state.voice (no ElevenLabs)."""

import base64
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.routes.assistant import SpeechCache
from app.assistant.voice import SAMPLE_RATE, TTS_SPEED, VoiceError
from app.config import Settings
from app.main import create_app

PCM = b"\x01\x00\xff\x7f"


class FakeVoice:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, float]] = []
        self.fail = fail

    def synthesize(self, text: str, speed: float = 1.0) -> bytes:
        self.calls.append((text, speed))
        if self.fail:
            raise VoiceError("TTS", 500)
        return PCM


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


def with_voice(client: TestClient, voice: FakeVoice | None) -> FakeVoice | None:
    client.app.state.voice = voice  # type: ignore[attr-defined]
    return voice


def test_503_when_voice_is_off(client: TestClient) -> None:
    with_voice(client, None)
    response = client.post("/api/assistant/speak", json={"text": "Turn left."})
    assert response.status_code == 503
    assert "ElevenLabs" in response.json()["detail"]


def test_success_then_cache_hit(client: TestClient) -> None:
    voice = with_voice(client, FakeVoice())
    assert voice is not None
    body = {"text": "Go through the door ahead."}
    first = client.post("/api/assistant/speak", json=body)
    second = client.post("/api/assistant/speak", json=body)
    assert first.status_code == 200 and second.status_code == 200, first.text
    a, b = first.json(), second.json()
    assert base64.b64decode(a["audio_pcm16"]) == PCM and a["audio_pcm16"] == b["audio_pcm16"]
    assert a["sample_rate"] == SAMPLE_RATE and a["cached"] is False and b["cached"] is True
    assert isinstance(a["tts_ms"], float)
    assert voice.calls == [("Go through the door ahead.", 1.1)]  # default speed 1.1, called once


def test_different_speed_is_a_different_cache_entry(client: TestClient) -> None:
    voice = with_voice(client, FakeVoice())
    assert voice is not None
    client.post("/api/assistant/speak", json={"text": "Turn right.", "speed": 1.0})
    client.post("/api/assistant/speak", json={"text": "Turn right.", "speed": 1.001})  # rounds
    client.post("/api/assistant/speak", json={"text": "Turn right.", "speed": 0.9})
    assert voice.calls == [("Turn right.", 1.0), ("Turn right.", 0.9)]


@pytest.mark.parametrize("text", ["", "x" * 241])
def test_text_length_is_validated(client: TestClient, text: str) -> None:
    voice = with_voice(client, FakeVoice())
    assert voice is not None
    response = client.post("/api/assistant/speak", json={"text": text})
    assert response.status_code == 422
    assert voice.calls == []


def test_blank_text_after_cleanup_is_rejected(client: TestClient) -> None:
    with_voice(client, FakeVoice())
    assert client.post("/api/assistant/speak", json={"text": "  ** "}).status_code == 422


@pytest.mark.parametrize(
    ("speed", "expected"), [(5.0, TTS_SPEED["max"]), (0.1, TTS_SPEED["min"]), (0.95, 0.95)]
)
def test_speed_is_clamped(client: TestClient, speed: float, expected: float) -> None:
    voice = with_voice(client, FakeVoice())
    assert voice is not None
    response = client.post("/api/assistant/speak", json={"text": "Arrived.", "speed": speed})
    assert response.status_code == 200, response.text
    assert response.json()["speed"] == expected
    assert voice.calls == [("Arrived.", expected)]


def test_voice_error_is_502_and_not_cached(client: TestClient) -> None:
    voice = with_voice(client, FakeVoice(fail=True))
    assert voice is not None
    for _ in range(2):
        response = client.post("/api/assistant/speak", json={"text": "Take the lift."})
        assert response.status_code == 502
        assert "ElevenLabs TTS failed" in response.json()["detail"]
    assert len(voice.calls) == 2


def test_lru_evicts_oldest() -> None:
    cache = SpeechCache(size=2)
    cache.put(("a", 1.1), "A")
    cache.put(("b", 1.1), "B")
    assert cache.get(("a", 1.1)) == "A"  # refreshes "a"
    cache.put(("c", 1.1), "C")
    assert cache.get(("b", 1.1)) is None
    assert cache.get(("a", 1.1)) == "A" and cache.get(("c", 1.1)) == "C"


def test_tts_gets_units_spelled_out(client: TestClient) -> None:
    voice = with_voice(client, FakeVoice())
    assert voice is not None
    response = client.post(
        "/api/assistant/speak", json={"text": "Turn left in 21 m into Office 0.38.", "speed": 1.0}
    )
    assert response.status_code == 200, response.text
    assert voice.calls == [("Turn left in 21 metres into Office 0.38.", 1.0)]

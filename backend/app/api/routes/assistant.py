"""Assistant endpoints: one conversation turn, human confirmation, reset, speech-to-text and
short spoken navigation hints."""

import base64
import binascii
import math
import secrets
import threading
from collections import OrderedDict
from time import perf_counter
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.assistant.port import NavigationError
from app.assistant.service import Assistant, AssistantError
from app.assistant.voice import SAMPLE_RATE, TTS_SPEED, ElevenLabsVoice, VoiceError, spoken

router = APIRouter(prefix="/assistant", tags=["assistant"])
STALE = {"revision_conflict", "stale_revision"}
SPEAK_SPEED = 1.1  # guidance hints default slightly faster than chat replies
SPEAK_CACHE_SIZE = 256
_cache_lock = threading.Lock()


class TurnRequest(BaseModel):
    # Optional on the first turn (contract): the server starts a session and returns its id.
    session_id: str | None = Field(
        default=None, min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"
    )
    text: str = Field(min_length=1, max_length=2000)
    ui: dict[str, Any] = Field(default_factory=dict)
    speak: bool = False
    voice: bool = False
    tts_speed: float = Field(default=TTS_SPEED["default"], ge=TTS_SPEED["min"], le=TTS_SPEED["max"])


class ConfirmRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    proposal_id: str = Field(min_length=1, max_length=32)
    approved: bool


class ResetRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class TranscribeRequest(BaseModel):
    audio: str = Field(max_length=700_000, description="Base64 WAV, 16 kHz mono, 0.1-10 s")


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=240)
    # Out-of-range speeds are clamped to TTS_SPEED rather than rejected (hints must never fail).
    speed: float = Field(default=SPEAK_SPEED, allow_inf_nan=False)


class SpeechCache:
    """Small thread-safe LRU of synthesized hints keyed by (text, speed rounded to 0.01)."""

    def __init__(self, size: int = SPEAK_CACHE_SIZE) -> None:
        self.size = size
        self.items: OrderedDict[tuple[str, float], str] = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key: tuple[str, float]) -> str | None:
        with self.lock:
            value = self.items.get(key)
            if value is not None:
                self.items.move_to_end(key)
            return value

    def put(self, key: tuple[str, float], value: str) -> None:
        with self.lock:
            self.items[key] = value
            self.items.move_to_end(key)
            while len(self.items) > self.size:
                self.items.popitem(last=False)


def _speech_cache(request: Request) -> SpeechCache:
    with _cache_lock:
        cache: SpeechCache | None = getattr(request.app.state, "speech_cache", None)
        if cache is None:
            cache = request.app.state.speech_cache = SpeechCache()
        return cache


def clamp_speed(speed: float) -> float:
    if not math.isfinite(speed):
        return SPEAK_SPEED
    return round(min(TTS_SPEED["max"], max(TTS_SPEED["min"], speed)), 2)


def _assistant(request: Request) -> Assistant:
    assistant: Assistant | None = getattr(request.app.state, "assistant", None)
    if assistant is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Assistant not available")
    return assistant


def _voice(request: Request) -> ElevenLabsVoice | None:
    voice: ElevenLabsVoice | None = getattr(request.app.state, "voice", None)
    return voice


def _nav_error(error: NavigationError) -> HTTPException:
    code = status.HTTP_409_CONFLICT if error.code in STALE else 422
    return HTTPException(code, {"code": error.code, "message": error.message})


@router.get("/status")
def assistant_status(request: Request) -> dict[str, Any]:
    assistant: Assistant | None = getattr(request.app.state, "assistant", None)
    return {
        "live": bool(assistant and assistant.transport),
        "voice": _voice(request) is not None,
        "model": assistant.model if assistant else None,
        "effort": assistant.effort if assistant else None,
        "tts_speed": TTS_SPEED,
    }


# Plain `def` routes: FastAPI runs them in its threadpool, so blocking model/voice calls don't
# stall the event loop.
@router.post("/turn")
def turn(body: TurnRequest, request: Request) -> dict[str, Any]:
    assistant = _assistant(request)
    session_id = body.session_id or secrets.token_urlsafe(16)
    try:
        value = assistant.turn(session_id, body.text, body.ui, body.voice)
    except AssistantError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from None
    except NavigationError as error:
        raise _nav_error(error) from None
    value["session_id"] = session_id
    value["audio_pcm16"], value["sample_rate"] = None, SAMPLE_RATE
    voice = _voice(request)
    if body.speak and voice is not None and value["reply"]:
        began = perf_counter()
        try:
            pcm = voice.synthesize(spoken(value["reply"]), body.tts_speed)
            value["audio_pcm16"] = base64.b64encode(pcm).decode("ascii")
        except VoiceError as error:
            value["voice_error"] = str(error)
        value["timing_ms"]["tts_ms"] = round((perf_counter() - began) * 1000, 1)
    return value


@router.post("/confirm")
def confirm(body: ConfirmRequest, request: Request) -> dict[str, Any]:
    try:
        return _assistant(request).confirm(body.session_id, body.proposal_id, body.approved)
    except NavigationError as error:
        raise _nav_error(error) from None


@router.post("/reset")
def reset(body: ResetRequest, request: Request) -> dict[str, str]:
    _assistant(request).reset(body.session_id)
    return {"status": "reset"}


@router.post("/speak")
def speak(body: SpeakRequest, request: Request) -> dict[str, Any]:
    """Short guidance hint (door, turn, floor change, arrival) as 16-bit PCM; cached per app."""
    voice = _voice(request)
    if voice is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Voice is off: no ElevenLabs key on the server."
        )
    text = spoken(body.text)
    if not text:
        raise HTTPException(422, "Text must not be empty.")
    speed = clamp_speed(body.speed)
    cache = _speech_cache(request)
    key = (text, speed)
    began = perf_counter()
    audio = cache.get(key)
    cached = audio is not None
    if audio is None:
        try:
            pcm = voice.synthesize(text, speed)
        except VoiceError as error:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(error)) from None
        audio = base64.b64encode(pcm).decode("ascii")
        cache.put(key, audio)
    return {
        "audio_pcm16": audio,
        "sample_rate": SAMPLE_RATE,
        "speed": speed,
        "cached": cached,
        "tts_ms": round((perf_counter() - began) * 1000, 1),
    }


@router.post("/transcribe")
def transcribe(body: TranscribeRequest, request: Request) -> dict[str, Any]:
    voice = _voice(request)
    if voice is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Voice is off: no ElevenLabs key on the server."
        )
    try:
        wav = base64.b64decode(body.audio, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "Audio must be base64.") from None
    if (
        len(wav) < 44
        or wav[:4] != b"RIFF"
        or wav[8:12] != b"WAVE"
        or not 3200 <= len(wav) - 44 <= 320_000
    ):
        raise HTTPException(422, "Recording must be a 0.1 to 10 second 16 kHz mono WAV.")
    began = perf_counter()
    try:
        text = voice.transcribe(wav)
    except VoiceError as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(error)) from None
    return {"text": text, "stt_ms": round((perf_counter() - began) * 1000, 1)}

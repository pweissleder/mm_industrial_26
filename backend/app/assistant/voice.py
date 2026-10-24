"""ElevenLabs speech-to-text and text-to-speech over the official REST endpoints.

Ported from the voice POC (unchanged models and formats), plus the supported TTS speed setting.
"""

import logging
import re

import httpx2

TTS_SPEED = {
    "min": 0.7,
    "max": 1.2,
    "default": 1.0,
}  # range ElevenLabs accepts for voice_settings.speed
SAMPLE_RATE = 16000


class VoiceError(RuntimeError):
    def __init__(self, stage: str, status: int | None = None) -> None:
        self.stage, self.http_status = stage, status
        detail = f" (HTTP {status})." if status else " (connection, timeout or invalid response)."
        super().__init__(f"ElevenLabs {stage} failed{detail}")


class ElevenLabsVoice:
    def __init__(self, api_key: str, voice_id: str, client: httpx2.Client | None = None) -> None:
        if not re.fullmatch(r"[A-Za-z0-9]{10,64}", voice_id):
            raise ValueError("Invalid ElevenLabs voice ID.")
        self.voice_id = voice_id
        if client is None:
            for name in ("httpx2", "httpcore2"):
                logger = logging.getLogger(name)
                logger.handlers.clear()
                logger.addHandler(logging.NullHandler())
                logger.propagate = False
            client = httpx2.Client(
                base_url="https://api.elevenlabs.io",
                headers={"xi-api-key": api_key},
                timeout=30.0,
                follow_redirects=False,
            )
        self.client = client

    def transcribe(self, wav: bytes) -> str:
        try:
            response = self.client.post(
                "/v1/speech-to-text",
                data={"model_id": "scribe_v2", "language_code": "en", "tag_audio_events": "false"},
                files={"file": ("speech.wav", wav, "audio/wav")},
            )
            if response.status_code != 200:
                raise VoiceError("STT", response.status_code)
            text = response.json().get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 2000:
                raise VoiceError("STT")
            return text.strip()
        except VoiceError:
            raise
        except Exception:
            raise VoiceError("STT") from None

    def synthesize(self, text: str, speed: float = TTS_SPEED["default"]) -> bytes:
        if not text or len(text) > 900:
            raise VoiceError("TTS text limit")
        speed = min(TTS_SPEED["max"], max(TTS_SPEED["min"], float(speed)))
        try:
            response = self.client.post(
                f"/v1/text-to-speech/{self.voice_id}",
                params={"output_format": f"pcm_{SAMPLE_RATE}"},
                json={
                    "text": text,
                    "model_id": "eleven_flash_v2_5",
                    "language_code": "en",
                    "voice_settings": {"speed": speed},
                },
            )
            if response.status_code != 200:
                raise VoiceError("TTS", response.status_code)
            pcm = response.content
            if "json" in response.headers.get("content-type", "") or not pcm or len(pcm) % 2:
                raise VoiceError("TTS audio format")
            return pcm
        except VoiceError:
            raise
        except Exception:
            raise VoiceError("TTS") from None

    def close(self) -> None:
        self.client.close()


def spoken(text: str, limit: int = 900) -> str:
    """Text for TTS: markdown removed, shortened at a sentence boundary; the screen keeps the full
    text.
    """
    text = re.sub(r"[*_`#]+", "", text).strip()
    if len(text) <= limit:
        return text
    cut = max(text.rfind(". ", 0, limit - 1), text.rfind("? ", 0, limit - 1))
    return text[: cut + 1] if cut > 100 else text[: limit - 1] + "..."

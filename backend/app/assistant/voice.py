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
    def __init__(self, stage: str, status: int | None = None, reason: str | None = None) -> None:
        self.stage, self.http_status, self.reason = stage, status, reason
        detail = f" (HTTP {status})." if status else " (connection, timeout or invalid response)."
        if reason == "quota_exceeded":
            detail = " (HTTP 401): the ElevenLabs API key's credit quota is used up."
        elif reason:
            detail = f" (HTTP {status}, {reason})."
        super().__init__(f"ElevenLabs {stage} failed{detail}")


def _reason(response: httpx2.Response) -> str | None:
    """ElevenLabs' machine-readable error status (e.g. quota_exceeded), never the message text."""
    try:
        detail = response.json().get("detail")
    except Exception:
        return None
    status = detail.get("status") if isinstance(detail, dict) else None
    return status if isinstance(status, str) and re.fullmatch(r"[a-z_]{3,40}", status) else None


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
                raise VoiceError("STT", response.status_code, _reason(response))
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
                raise VoiceError("TTS", response.status_code, _reason(response))
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


# Units and symbols the TTS voice reads badly ("21 m" as the letter m), spelled out for speech
# only. A unit is expanded only directly after a number that is not part of a label or id
# ("Office 0.38", "H-A1-A", "room", "5 minutes" stay as they are).
_NUMBER = r"(?<![\w.])(\d+(?:[.,]\d+)?)"
_UNITS = {
    "km": ("kilometre", "kilometres"),
    "cm": ("centimetre", "centimetres"),
    "mm": ("millimetre", "millimetres"),
    "m²": ("square metre", "square metres"),
    "m2": ("square metre", "square metres"),
    "m": ("metre", "metres"),
}
_UNIT = re.compile(_NUMBER + r" ?(km|cm|mm|m²|m2|m)(?![\w²³])")
_ABOUT = re.compile(r"(?:~|≈|(?<![\w.])ca\.)\s*(?=\d)")
_DEGREES = re.compile(_NUMBER + r" ?°")
_PERCENT = re.compile(_NUMBER + r" ?%")
_BY = re.compile(_NUMBER + r" ?[×x] ?(?=\d)")  # dimensions: "3 x 4" -> "3 by 4"
_TIMES = re.compile(_NUMBER + r" ?[×x](?!\w)")  # factors: "2×" -> "2 times"


def _say_number(number: str, one: str, many: str) -> str:
    return f"{number} {one if number == '1' else many}"


def speakable(text: str) -> str:
    """Text as it should be read aloud: units and symbols after numbers spelled out in English
    ("21 m" -> "21 metres", "~5 m²" -> "about 5 square metres", "90°" -> "90 degrees"), list
    separators as commas and arrows as "to". Labels and ids are left alone."""
    text = _ABOUT.sub("about ", text)
    text = _UNIT.sub(lambda m: _say_number(m.group(1), *_UNITS[m.group(2)]), text)
    text = _DEGREES.sub(lambda m: _say_number(m.group(1), "degree", "degrees"), text)
    text = _PERCENT.sub(lambda m: f"{m.group(1)} percent", text)
    text = _BY.sub(lambda m: f"{m.group(1)} by ", text)
    text = _TIMES.sub(lambda m: f"{m.group(1)} times", text)
    text = re.sub(r"\s*°", " degrees", text)
    text = re.sub(r"\s*%", " percent", text)
    text = re.sub(r"\s*(?:->|→)\s*", " to ", text)
    text = re.sub(r"\s*[·•|]\s*", ", ", text)
    text = re.sub(r"(?:,\s*){2,}", ", ", text)  # empty items between separators
    text = re.sub(r"^\s*,\s*|\s*,\s*$", "", text)
    text = re.sub(r",\s*([.!?])", r"\1", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def spoken(text: str, limit: int = 900) -> str:
    """Text for TTS: markdown removed, units and symbols spelled out (`speakable`), shortened at a
    sentence boundary; the screen keeps the original text.
    """
    text = speakable(re.sub(r"[*_`#]+", "", text).strip())
    if len(text) <= limit:
        return text
    cut = max(text.rfind(". ", 0, limit - 1), text.rfind("? ", 0, limit - 1))
    return text[: cut + 1] if cut > 100 else text[: limit - 1] + "..."

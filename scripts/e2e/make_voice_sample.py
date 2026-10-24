"""Synthesise a spoken test question with ElevenLabs (the app's own voice client) as a 16 kHz
mono WAV, for the browser voice test (Chrome's fake microphone plays it).

    ELEVENLABS_API_KEY=... python scripts/e2e/make_voice_sample.py "<text>" <out.wav>

The key is read from the environment only (Make-VoiceSample.ps1 injects the DPAPI key).
Leading/trailing silence is added so push-to-talk captures the whole sentence.
"""

import os
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from app.assistant.voice import SAMPLE_RATE, ElevenLabsVoice  # noqa: E402


def main() -> None:
    text, out = sys.argv[1], Path(sys.argv[2])
    voice = ElevenLabsVoice(os.environ["ELEVENLABS_API_KEY"], "JBFqnCBsd6RMkjVDRZzb")
    pcm = voice.synthesize(text, 1.0)
    silence = b"\x00\x00" * (SAMPLE_RATE // 2)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(silence + pcm + silence * 4)
    print(f"{out} {len(pcm) / 2 / SAMPLE_RATE:.1f} s speech")


if __name__ == "__main__":
    main()

"""Build the two submission videos (demo, tech) from REAL app recordings and screenshots.

- Voice-over: ElevenLabs through the running backend's /api/assistant/speak (the key stays in
  the backend process; needs Start-AriNav.ps1 -Live). Each sentence is cached as WAV.
- Every segment lasts as long as its narration (+ a short pause): clips are sped up to fit
  (never slowed below real time; a still frame fills the rest), cards are rendered with Pillow.
- Output: <out>/<name>.mp4 (H.264 + AAC, 1280x720, 25 fps) and <out>/<name>.srt subtitles.
Run (Pillow + imageio-ffmpeg on PYTHONPATH):
  python scripts/media/build_videos.py --raw <dir with scene*.webm> --shots docs/night \
      --out <media dir> [--only demo|tech]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
W, H, FPS = 1280, 720, 25
API = "http://localhost:8000/api/assistant/speak"
SPEED = 1.1
FONT = "C:/Windows/Fonts/segoeui.ttf"
FONT_B = "C:/Windows/Fonts/seguisb.ttf"
NAVY, ORANGE, INK, MUTED = (0, 36, 120), (255, 122, 26), (20, 28, 48), (92, 102, 125)

# ------------------------------------------------------------------ plans
# clip: (source webm, start s, end s); card: title, lines, optional image (path relative to --shots)
DEMO = [
    {"card": ("AriNav", ["Talk to any building.", "Indoor navigation grounded in the building data."]),
     "say": "AriNav turns a building file into an indoor navigator you can talk to."},
    {"clip": ("scene1", 7.0, 17.0),
     "say": "I say where I am and ask for a coffee. It finds the nearest machine, about twenty "
            "metres away."},
    {"clip": ("scene1", 17.0, 26.3),
     "say": "One more question, and the official route appears, with step by step directions."},
    {"clip": ("scene1", "walk0", "walk1"),
     "say": "I start the walkthrough: a guidance card, short spoken hints, and a first person "
            "view at eye height. It is a labelled simulation, not live positioning."},
    {"clip": ("scene1", "arrive0", "end"), "say": "And I have arrived."},
    {"clip": ("scene2", 5.0, 29.5),
     "say": "Now the only lift is closed. For a wheelchair user, AriNav says honestly that there "
            "is no step free way upstairs. It never falls back to the stairs."},
    {"clip": ("scene2", 29.5, "end"),
     "say": "Instead it offers a step free destination on the same floor."},
    {"clip": ("scene3", "walk0", "ask0"),
     "say": "I can also walk myself, with the keyboard or a touch joystick. Walls stop me."},
    {"clip": ("scene3", "ask0", "end"),
     "say": "Asked 'What's on my right?', AriNav answers from the geometry around the person's "
            "position and view direction, and never claims to see through walls.",
     "silent": 7.0},
    {"card": ("Grounded. Accessible. Honest.",
              ["Official kit router · Claude tool loop with hard guards",
               "Step-free and width constraints bind every answer",
               "Simulation, sensors and estimates are always labelled"]),
     "say": "Grounded in the data. Accessible by default. Honest about what it knows.",
     "silent": 4.5},
]

TECH = [
    {"card": ("How AriNav works", ["One building, one scenario, one source of truth"],
              "night/e2e/HQ-proc-factory-dev-003-1440.png"),
     "say": "AriNav keeps one building and one scenario as the single source of truth for the "
            "map, the router and the assistant."},
    {"card": ("Pipeline", ["building.json -> validate & clean -> graph -> 3D model",
                           "Deterministic offline, about six seconds per building",
                           "Saved by building id; reopening never regenerates",
                           "Explicit Rebuild always runs; moved things mark HQ outdated"],
              "night/e2e/HQ-Q05-dialog-1440.png"),
     "say": "A pipeline validates the data and builds a room graph and a 3D model, stored by "
            "building id. Rebuild always runs, and a moved thing marks the high quality view as "
            "outdated."},
    {"card": ("Routing", ["Official kit 1.1.0 router, pinned and vendored",
                          "Benchmark 655/655 · routes 1338/1338",
                          "locate 553/553 · count 239/239",
                          "140 seeded cases with independent path checks"]),
     "say": "Routes come from the official kit router. The harness still scores six hundred "
            "fifty five out of six hundred fifty five, and a hundred forty seeded cases check "
            "every path independently."},
    {"card": ("Assistant", ["Claude Sonnet 5.5 with typed tools",
                            "Guards: step-free never loosened, no stairs fallback",
                            "Pose-aware 'what's on my right' from geometry",
                            "60 offline state scenarios · 20 live dialogs, $0.30"],
              "night/scenes/S2-2-no-step-free-route.png"),
     "say": "Claude calls typed tools. Hard guards keep wheelchair constraints binding, and side "
            "questions are answered from geometry. Sixty offline scenarios and twenty live "
            "dialogs test it."},
    {"card": ("Immersive client", ["three.js first person at 1.65 m · minimap · split view",
                                   "WASD / touch joystick · wall sliding, no tunneling",
                                   "Phone rotation behind a permission button",
                                   "Profiles: Core · Immersive · Showcase"],
              "night/e2e/Q02-reference-1000x860-pov.png"),
     "say": "The client adds a first person view, walking with collision, and phone rotation "
            "behind a permission. Three profiles switch modules, so the core always works alone."},
    {"card": ("Honest limits", ["Positions are simulated or stated, never measured",
                                "Real phones and microphones: not tested tonight",
                                "Frame times measured in software rendering only"]),
     "say": "Limits stay explicit: positions are simulated or stated, and real phone sensors are "
            "not tested yet."},
]

# Moments inside the recordings (seconds), set after looking at the contact sheets.
MARKS: dict[str, dict[str, float]] = {}


# ------------------------------------------------------------------ helpers
def run(args: list[str]) -> None:
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def duration(path: Path) -> float:
    out = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    for line in out.stderr.splitlines():
        if "Duration:" in line:
            h, m, s = line.split("Duration:")[1].split(",")[0].strip().split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    raise RuntimeError(f"no duration for {path}")


def tts(text: str, cache: Path) -> tuple[Path, float]:
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / (hashlib.sha1(text.encode()).hexdigest()[:16] + ".wav")
    if not path.exists():
        # the endpoint takes short hints (<= 240 chars): one request per sentence
        pcm, rate = b"", 16000
        for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
            body = json.dumps({"text": sentence, "speed": SPEED}).encode()
            req = urllib.request.Request(API, body, {"Content-Type": "application/json"})
            for attempt in range(3):  # ElevenLabs answers 502 now and then
                try:
                    with urllib.request.urlopen(req, timeout=60) as res:
                        data = json.loads(res.read())
                    break
                except urllib.error.HTTPError as error:
                    if attempt == 2 or error.code != 502:
                        raise SystemExit(f"TTS failed ({error.code}) for: {sentence}") from None
                    time.sleep(2)
            rate = int(data["sample_rate"])
            pcm += base64.b64decode(data["audio_pcm16"]) + bytes(2) * int(rate * 0.18)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm)
    with wave.open(str(path)) as w:
        return path, w.getnframes() / w.getframerate()


def wrap(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        test = f"{line} {word}".strip()
        if font.getlength(test) <= width or not line:
            line = test
        else:
            lines.append(line)
            line = word
    return [*lines, line] if line else lines


def card(title: str, lines: list[str], image: Path | None, out: Path) -> None:
    img = Image.new("RGB", (W, H), (246, 248, 253))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 10], fill=ORANGE)
    big = ImageFont.truetype(FONT_B, 52 if image else 72)
    small = ImageFont.truetype(FONT, 26 if image else 34)
    x = 70
    text_w = W - 2 * x
    if image and image.exists():
        shot = Image.open(image).convert("RGB")
        shot.thumbnail((600, 470))
        left, top = W - shot.width - 50, (H - shot.height) // 2
        img.paste(shot, (left, top))
        d.rectangle([left - 1, top - 1, left + shot.width, top + shot.height], outline=(200, 207, 222))
        text_w = left - x - 40
        y = 170
    else:
        y = 230
    for part in wrap(title, big, text_w):
        d.text((x, y), part, font=big, fill=NAVY)
        y += big.size + 8
    y += 30
    for line in lines:
        rows = wrap(line, small, text_w - (24 if image else 0))
        if image:
            d.ellipse([x, y + 12, x + 10, y + 22], fill=ORANGE)
        for row in rows:
            d.text((x + (24 if image else 0), y), row, font=small, fill=INK if image else MUTED)
            y += small.size + 8
        y += 14
    d.text((x, H - 50), "AriNav · night build · real app captures",
           font=ImageFont.truetype(FONT, 18), fill=MUTED)
    img.save(out)


def badge(text: str, out: Path) -> None:
    """Small corner label for time-compressed clips (honest about speed-ups)."""
    font = ImageFont.truetype(FONT_B, 20)
    w = int(font.getlength(text)) + 28
    img = Image.new("RGBA", (w, 38), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, 37], radius=18, fill=(0, 36, 120, 215))
    d.text((14, 6), text, font=font, fill=(255, 255, 255, 255))
    img.save(out)


def caption(text: str, out: Path) -> None:
    font = ImageFont.truetype(FONT_B, 26)
    rows = wrap(text, font, 680)
    h = 24 + len(rows) * 36
    img = Image.new("RGBA", (740, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, 739, h - 1], radius=16, fill=(0, 36, 120, 225))
    for k, row in enumerate(rows):
        d.text((30, 12 + k * 36), row, font=font, fill=(255, 255, 255, 255))
    img.save(out)


def mark(src: str, value: float | str, raw: dict[str, Path]) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if value == "end":
        return duration(raw[src]) - 0.2
    return MARKS[src][value]


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def build(name: str, plan: list[dict], raw: dict[str, Path], shots: Path, out: Path) -> Path:
    work = out / "work" / name
    work.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    subs: list[str] = []
    clock = 0.0
    for i, seg in enumerate(plan):
        if seg.get("silent"):
            # no voice-over (TTS quota): the text is shown on screen and in the subtitles
            target = speech = float(seg["silent"])
            wav = work / f"{i:02}-silence.wav"
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(bytes(2 * int(16000 * target)))
        else:
            wav, speech = tts(seg["say"], out / "work" / "tts")
            target = max(2.5, speech + 0.45)
        part = work / f"{i:02}.mp4"
        audio = ["-i", str(wav), "-filter_complex", f"[1:a]apad,atrim=0:{target:.3f}[a]"]
        if "card" in seg:
            title, lines, *img = seg["card"]
            png = work / f"{i:02}.png"
            card(title, lines, shots / img[0] if img else None, png)
            run(["-loop", "1", "-t", f"{target:.3f}", "-i", str(png), *audio, "-map", "0:v",
                 "-map", "[a]", "-r", str(FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-ar", "44100", "-t", f"{target:.3f}", str(part)])
        else:
            src, a, b = seg["clip"]
            t0, t1 = mark(src, a, raw), mark(src, b, raw)
            factor = max(1.0, (t1 - t0) / target)
            hold = max(0.0, target - (t1 - t0) / factor)
            vf = (f"setpts=(PTS-STARTPTS)/{factor:.4f},tpad=stop_mode=clone:stop_duration="
                  f"{hold + 0.1:.3f},fps={FPS},scale={W}:{H}")
            extra: list[str] = []
            if seg.get("silent"):
                png = work / f"{i:02}-caption.png"
                caption(seg["say"], png)
                extra = ["-i", str(png)]
                graph = f"[0:v]{vf}[c];[c][2:v]overlay=(W-w)/2:150,format=yuv420p[v]"
            elif factor > 1.15:  # label time-compressed footage
                png = work / f"{i:02}-badge.png"
                badge(f"{factor:.1f}x speed", png)
                extra = ["-i", str(png)]
                graph = f"[0:v]{vf}[c];[c][2:v]overlay=24:{H - 62},format=yuv420p[v]"
            else:
                graph = f"[0:v]{vf},format=yuv420p[v]"
            run(["-ss", f"{t0:.3f}", "-to", f"{t1:.3f}", "-i", str(raw[src]), *audio[:2], *extra,
                 "-filter_complex", f"{graph};[1:a]apad,atrim=0:{target:.3f}[a]",
                 "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-c:a", "aac", "-ar", "44100",
                 "-t", f"{target:.3f}", str(part)])
        subs.append(f"{i + 1}\n{srt_time(clock + 0.1)} --> {srt_time(clock + speech + 0.1)}\n"
                    f"{seg['say']}\n")
        clock += target
        parts.append(part)
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    final = out / f"{name}.mp4"
    run(["-f", "concat", "-safe", "0", "-i", str(lst), "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-movflags", "+faststart", str(final)])
    (out / f"{name}.srt").write_text("\n".join(subs), encoding="utf-8")
    return final


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--shots", type=Path, default=Path("docs"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--marks", type=Path, help="JSON {scene: {mark: seconds}}")
    parser.add_argument("--only", choices=["demo", "tech"])
    args = parser.parse_args()
    if args.marks:
        MARKS.update(json.loads(args.marks.read_text(encoding="utf-8")))
    raw = {
        "scene1": args.raw / "scene1_coffee_route_arrival.webm",
        "scene2": args.raw / "scene2_wheelchair_lift_closed.webm",
        "scene3": args.raw / "scene3_walk_whats_on_my_right.webm",
    }
    for name, plan in (("arinav_demo_60s", DEMO), ("arinav_tech_60s", TECH)):
        if args.only and not name.startswith(f"arinav_{args.only}"):
            continue
        final = build(name, plan, raw, args.shots, args.out)
        print(f"{final}  {duration(final):.1f} s")


if __name__ == "__main__":
    main()

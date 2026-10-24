# Unified navigation + voice prototype (Noah)

Standalone proof of concept, kept separate from `backend/` and `frontend/`. One local server,
one deterministic building state shared by the map controls, a text chat, Claude tool calls and
push-to-talk voice. Nothing here is wired into the FastAPI/Astro app yet.

| Part | Files |
|---|---|
| Deterministic core | `data.py` (read-only kit access), `service.py` (RAM scenarios), `navigation.py` + `geometry.py` + `validation.py` + `diagnostics.py` (precision obstacle router), `challenge.py` (organizer route rules), `operations.py` (nine benchmark operations) |
| Agent | `assistant.py` — Claude Messages API, `claude-sonnet-5-5`, effort `medium`, 7 strict tools, bounded history + explicit session state, human confirmation before any change |
| Voice | `voice.py` — ElevenLabs STT (`scribe_v2`) / TTS (`eleven_flash_v2_5`); `recorder.js` AudioWorklet |
| Server + UI | `server.py` (stdlib, loopback only), `index.html`, `app.js`, `assistant.js`, `style.css` (vanilla, English UI) |
| Tests | `test_*.py` (offline, no API calls); `tools/live_check.py`, `tools/voice_check.py` (live, cost money) |

## Setup

Python 3.12+ (tested with 3.13.5), Node.js only for the UI tests.

```bash
cd prototypes/unified-nav-voice
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # macOS/Linux: .venv/bin/python
```

Data: the official kit **v1.0.0 ZIP** (`industrial-agents-hackathon-2026-main.zip`) and a checkout of
the starter kit at commit `cb51a6f` (for `starter_agent/building_map.py` and `benchmark/score.py`).
Defaults are `~/Downloads/industrial-agents-hackathon-2026-main.zip` and
`~/Documents/HackathonTransfer/SyneraStarterReference`; override with `SYNERA_KIT_ZIP` and
`SYNERA_REFERENCE_DIR`. The kit data is not copied into this repo. See `KIT_1_1_0_IMPACT.md` for the
v1.1.0 patch.

## Run

Offline (no keys, no cost; chat says "offline", map/router/benchmark work):

```bash
.venv/Scripts/python -B server.py --port 8770 --offline
```

Live: the server reads `ANTHROPIC_API_KEY` and `ELEVENLABS_API_KEY` from its own environment and
removes them from `os.environ` after creating the clients; keys never reach the browser. On
Windows the keys can be stored with DPAPI instead of a `.env` file:

```powershell
pwsh -File scripts/Register-ApiKey.ps1 -Provider Anthropic    # once, hidden input
pwsh -File scripts/Register-ApiKey.ps1 -Provider ElevenLabs   # once
pwsh -File scripts/Start-Lab.ps1 -Live                        # http://127.0.0.1:8770
```

## Use

- **Assistant panel:** type, or hold *Hold to talk* (max 10 s). Each tool call, its result, timing and
  estimated cost are shown. *Example questions* run on building proc-factory-dev-003.
- Changes (close/open a room, door, lift) appear as a card; only *Confirm* applies them.
- **Calculation mode:** *Precision* (obstacles, width, safety margin; procedural buildings only),
  *Official challenge* (room sequence, 0.15 m wall clearance, furniture ignored, stairs/lift costs),
  *Starter-kit reference*. Map click → *Set as start / target*. Tabs *Inspect building*, *Benchmark*.

## Tests

```bash
.venv/Scripts/python -B -m unittest discover     # offline: 123 tests, needs the kit ZIP + node
```

`test_assistant.py` replaces Claude with a scripted stand-in (marked offline) but uses the real
tools, data and router. Live checks: `tools/live_check.py --out live/x.json "Where is the CNC mill?"`.

## Integration gaps (honest list)

- Not connected to `backend/` (FastAPI/LangGraph) or `frontend/` (Astro); separate stdlib server and
  data access via the kit ZIP rather than the team's ingestion/storage.
- Challenge routing predates kit v1.1.0 in two details (door landing, extra production/forklift
  rules); see `KIT_1_1_0_IMPACT.md`. Benchmark `errand`/`what_if_door` still use the v1.0.0 model.
- Precision routing only for verified procedural buildings; vertical moves are schematic.
- Voice verified with a synthesised WAV through the real STT → Claude → router → TTS chain; a
  human microphone test in the browser is still open.

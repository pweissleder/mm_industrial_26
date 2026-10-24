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

Data (pinned to **kit 1.1.0**, starter `main` = `dc03c44`): buildings come from the official kit ZIP
(`industrial-agents-hackathon-2026-main.zip`, buildings unchanged in 1.1.0 and verified per blob);
`starter_agent/building_map.py`, `benchmark/score.py` and the Train/Dev `questions*.jsonl` come from a
checkout of the starter repo at `dc03c44`, verified by git blob id. Checkout setup:

```bash
git clone --filter=blob:none https://github.com/Synera-GmbH/industrial-agents-hackathon-2026 SyneraStarterReference
cd SyneraStarterReference && git sparse-checkout set --no-cone '/*' '!/*/' '/benchmark/' '/starter_agent/' \
  '/data/train/*/questions.jsonl' '/data/train/*/questions_basic.jsonl' '/data/dev/*/questions.jsonl' '/data/dev/*/questions_basic.jsonl'
```

Defaults are `~/Downloads/industrial-agents-hackathon-2026-main.zip` and
`~/Documents/HackathonTransfer/SyneraStarterReference`; override with `SYNERA_KIT_ZIP` and
`SYNERA_REFERENCE_DIR`. No kit data is copied into this repo. See `KIT_1_1_0_IMPACT.md`.

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
- **Calculation mode:** *Official routing* (default; exactly the kit 1.1.0 route model, dashed purple
  line, furniture ignored) · *Precision* (blue; obstacles with person/vehicle width and safety margin,
  profiles A/B/0 = stationary / snapshot / walls only, optional 1 m snapping of blocked points;
  procedural buildings only) · *Official graph (debug)*. Map click → *Set as start / target*.
  Tabs *Inspect building*, *Benchmark* (nine operations, Train/Dev examples). Everything works without
  the LLM; the assistant only chooses tools. Step-by-step directions under each reply come from the
  router, never from the model.

## Tests

```bash
.venv/Scripts/python -B -m unittest discover        # offline, needs the kit ZIP, checkout + node
.venv/Scripts/python -B benchmark_eval.py           # all Train+Dev benchmark + warm-up questions (~85 s)
.venv/Scripts/python -B sim/run_sim.py --offline    # 13 customer dialogs, scripted plan, real tools
pwsh -File scripts/Run-Sim.ps1 --live --label x     # same dialogs with real Claude (costs ~$0.26)
.venv/Scripts/python -B robustness_audit.py --label x   # precision routes vs independent geometry checker
```

`test_assistant.py` and `sim --offline` replace Claude with a scripted stand-in (marked offline) but use
the real tools, data and routers. Results and the round-3 report: `reports/ROUND3_REPORT.md`.

## Integration gaps (honest list)

- Not connected to `backend/` (FastAPI/LangGraph) or `frontend/` (Astro); separate stdlib server and
  data access via the kit ZIP rather than the team's ingestion/storage.
- Production-zone transit and forklift rules are not in kit 1.1.0; available only as opt-in,
  labelled unofficial.
- Precision routing only for verified procedural buildings; vertical moves are schematic.
- Indoor positioning: concept only, see `LOCALIZATION.md`.
- Voice verified with a synthesised WAV through the real STT → Claude → router → TTS chain; a
  human microphone test in the browser is still open.

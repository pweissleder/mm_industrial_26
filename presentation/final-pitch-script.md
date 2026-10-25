# AriNav final pitch: script (first iteration)

Format at the final: **3 minutes pitch + 1 minute Q&A**, at Tacto. Slides: [`final-pitch.html`](final-pitch.html), opened in a browser. Arrow keys or click to move, **F** for fullscreen, **Ctrl+P** saves a PDF.

Judging order, so the time is spent in the same order: **does it work (55)**, **interaction (25)**, **creativity (10)**, demo/explanation (10).

## Timing at a glance

| # | Slide | Time | Cumulative |
|---|---|---|---|
| 1 | Hook | 12 s | 0:12 |
| 2 | Four questions | 13 s | 0:25 |
| 3 | Live demo | 75 s | 1:40 |
| 4 | Does it work | 30 s | 2:10 |
| 5 | How it works | 25 s | 2:35 |
| 6 | What we added | 20 s | 2:55 |
| 7 | Close | 5 s | 3:00 |

Read aloud, the spoken text below runs about 2:50 at a calm pace, which leaves a little slack for the demo.

## Spoken text

### 1 · Hook (12 s)
> You walk into a factory looking for the 3D printer. The floor plan is a PDF, the machine list is a spreadsheet, and the one person who knows has gone home. We built AriNav: a map of any building that you can talk to.

### 2 · Four questions (13 s)
> The brief asked four things: find, route, edit and react. AriNav does all four in one conversation, by voice or text. Let me show you.

### 3 · Live demo (75 s)
Switch to the app, already open on Fertigung Ost 36. Say each line, then let the app answer. Don't narrate over the voice.

1. **"Where is the 3D printer?"**: there are nine, so it asks which one. *"Notice it asks instead of guessing."*
2. **"I'm in Office 2.04. Take me to the CNC mill, no stairs."**: the lift route animates across the floors and the walker follows with spoken hints.
3. **"The office lift is closed for maintenance."** → **Confirm**: the lift turns red. *"Now there's no step-free way down, and it tells you so, with the stairs as the alternative."*
4. **"Add a door between 2.04 and 2.05."**: it shows the door first and waits. → **Confirm**: the new connection appears.
5. **Developer profile**, drag a machine into another hall: walls stop it, pulling past moves it, **Save**. *"Machines move; the map keeps up."*

If anything stalls for more than ~5 s, switch to the backup video at the same step and keep talking.

### 4 · Does it work (30 s)
> Does it work? On the kit's training and dev buildings, the agent answers 650 of 655 benchmark questions end to end, and 99 percent of all 2,785 questions, with Claude Haiku, in 17 minutes, for eight dollars. Underneath, the navigation core is deterministic and gets every route right to the centimetre. And Sunday's building is one command: answers, scores and a report, no manual steps.

### 5 · How it works (25 s)
> The split is deliberate. Routing is deterministic: a graph of rooms, doors, stairs and lifts with exact walking distances. Claude is the interaction layer: it asks when something is vague, shows what it understood, and only changes the building after you confirm. A new building goes through an agentic pipeline: validated, cleaned, turned into a graph and a to-scale 3D model, with Opus checking each stage.

### 6 · What we added (20 s)
> On top: walk-along guidance with voice that re-routes when a door closes; a layout editor, so "this machine is now here" updates the map; three profiles, from just-ask-me to developer; and memory of past conversations. We tested it against tricky users: vague requests, speech-to-text errors, rooms that don't exist.

### 7 · Close (5 s)
> Any building, a map you can talk to. Thank you.

## Demo prep checklist

- [ ] The demo building is in the kit's `dev` split, not in `backend/data/datasets/`. Start the backend with `DATASETS_DIR=../problem_statement/industrial-agents-hackathon-2026/data/dev` in `backend/.env`, or copy `proc-factory-dev-012` into `backend/data/datasets/`.
- [ ] Rehearse each demo line on that building with the exact wording above; adjust the wording to what the agent handles reliably.
- [ ] Voice works (ElevenLabs key, microphone allowed on `localhost`); have text input ready as a fallback.
- [ ] Scenario reset before going on stage: the lift reopened and the 2.04/2.05 door not saved yet. Also restore the moved machine (`git checkout` the building file).
- [ ] Record the backup video of the same five steps.
- [ ] Laptop: notifications off, browser zoom 100 %, the app and the slides in two tabs.

## Facts behind the numbers

| Claim | Source |
|---|---|
| 650/655 benchmark, 2118/2130 warm-ups, 2,785 questions, 1051 s, $8.39, Claude Haiku 5.5 | `benchmark_results/graph-train-dev/report.md` (run 25 Oct 2026, 04:34) |
| 240/240 buildings, pipeline in 42 s | `benchmark_results/graph-ingest/report.md` |
| Deterministic core 655/655, 1,338/1,338 routes, cm-exact, kit 1.1.0 | `docs/reports/nav_benchmark_1_1_0.md` |
| 12/13 dialog scenarios (20/21 turns) | `docs/reports/dialog_sim_live_medium_v2.md` |
| One command | `SUBMISSION/BENCHMARK.md` |

Re-run before the final and update slide 4 if the numbers change.

## Likely questions (1 minute, answer in one or two sentences)

**How do you handle Sunday's unseen building?**
It goes through the same deterministic graph-only pipeline as the 240 kit buildings, which all passed with their geometry unchanged, then the same agent answers. One command, no manual steps.

**Why Haiku and not a bigger model?**
The hard part, routing, is deterministic, so the model only has to understand the question and pick the right tool. Haiku gets 99 % at under a cent a question; the benchmark command takes `--model` if we want Sonnet.

**What does the agent do with a vague request?**
It asks one question, only what it needs: which of the nine printers, or where you are right now. The dialog tests cover this.

**How do you stop it from changing the building by mistake?**
Every change is shown first and only applied after Confirm. Closures are scenario state; saved layout edits are written to the building file.

**What if the floor plan data is messy?**
The pipeline runs 21 checks and fixes what it safely can without moving geometry; anything it is unsure about is reported, and the full pipeline pauses for a human review.

**How does it know where I am?**
Today you tell it, or pick your spot on the map; the guidance then follows a simulated walker. Real positioning (QR codes at doors, Wi-Fi) is the natural next step.

**Multiple buildings?** <span>(to verify before the final)</span>
Every building is its own graph and the agent can switch building mid-conversation. Walkways between buildings: confirm the current state with the team.

## Open items for the next iteration

- Confirm the demo phrasing on the live agent, and whether "floor plan to building" (SVG reconstruction) is demo-ready; slide 6 marks it to confirm.
- Slides now use real app screenshots (`presentation/img/`, copied from `docs/reports/browser*`). Most show Fertigung Ost 27 / Laborgebäude 025; re-shoot on Fertigung Ost 36 if time allows, and add shots of the door edit and the layout editor ("this is now here"), which have none yet.
- Decide who presents which part (suggestion: one voice for slides, one person driving the demo).

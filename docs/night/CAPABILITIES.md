# AriNav — capabilities and modules (night build, 2026-10-25)

Labels: **PROVEN** = tested tonight with the evidence in `TEST_SUMMARY.md`. **SIMULATION** = works,
but positions/motion are simulated or stated, never measured. **NOT TESTED** = built, not verified
on real hardware. **EXPERIMENT/NOT BUILT** = intentionally left out.

## What a user can do

| Capability | Status |
|---|---|
| Ask in plain language (text; voice via ElevenLabs when quota allows) for places, routes, facts | PROVEN (live dialogs 19/20) |
| Official routes from the pinned kit 1.1.0 router; step-by-step directions | PROVEN (harness 655/1338/553/239, 140 seeded cases) |
| Wheelchair / step-free binds every answer; no stairs fallback; closed lift explained | PROVEN (offline + live + browser scene) |
| Width binding (trolley, precision mode) incl. nearest search | PROVEN offline (scenario), live answer slightly verbose |
| Closures with confirmation, one scenario for map, router, assistant | PROVEN |
| "What's on my right / behind me" from pose + view direction; occlusion; asks if heading unknown | PROVEN offline + live + browser scene |
| Leak → room directly below (`room_stacked`, no water simulation) | PROVEN live |
| Guidance card, 4-phase drone fly-through (camera only), simulated walk with short spoken hints | SIMULATION (labelled "Simulated walkthrough"); fly-through phases reviewed in screenshots |
| Turn words at doors ("Walk about 16 m, then turn left through the door into Kitchen 0.28."); left as plain text when the geometry is unclear | PROVEN (backend unit + real route) |
| Spoken text: units spelled out for the voice ("16 metres", "about", "degrees"); labels like "Office 0.38" kept | PROVEN (unit tests); ElevenLabs TTS answered HTTP 200 on 25.10. morning |
| First person at 1.65 m, minimap, developer split view | PROVEN render (browser screenshots) |
| Walk yourself (developer mode only): WASD / arrows / drag-look, speed 1–8 m/s, touch joystick, wall sliding, no tunnelling, rides with E/Q or tap | SIMULATION, PROVEN logic (54 + 990 cases), mode separation checked in the browser |
| An explicitly set start replaces the walked-to location; local routes and the assistant use the same start | PROVEN (unit + browser check) |
| Off-route while walking → replan from the person, no teleport | PROVEN (browser G05) |
| Phone rotation (permission button, calibration, truthful status incl. "needs HTTPS"); `Start-AriNav.ps1 -Phone` serves HTTPS on the LAN | NOT TESTED on a phone (synthetic events pass) |
| HQ showcase: 3 buildings, saved by building id, reopen without generation, explicit Rebuild, moved thing → "HQ outdated · showing LQ" | PROVEN (HQ suite 8/8) |
| Opt-in local session memory + recap (export, delete, new session; demo sessions labelled) | PROVEN (backend tests + browser recap) |
| Camera image localisation | NOT BUILT (budget/time; left as experiment) |

## Profiles (`?profile=` or `Start-AriNav.ps1 -Profile`)

| Profile | Modules | Use |
|---|---|---|
| `core` | assistant, routing, map, closures (no guidance card, no POV/game/orientation/memory) | fallback; proves new modules are optional (A02) |
| `immersive` (default) | core + guidance, first person, walk yourself, phone rotation | recommended for judging |
| `showcase` | immersive + session memory/recap | recordings and demos |

## Module map (new or changed tonight)

| Module | Files | Switch |
|---|---|---|
| Feature profiles | `frontend/src/lib/features.ts` | URL / env |
| Immersive view + input | `frontend/src/components/arinav/useImmersive.ts`, `ImmersiveControls.tsx`, `scene.ts` (POV pass, minimap, split) | `pov`, `game`, `orientation` |
| Walkable geometry | `frontend/src/lib/walkable.ts`, `spatial.ts` | used by `game` |
| Guidance (Phase B) + manual driver | `frontend/src/components/arinav/useGuidance.ts`, `lib/simulation.ts` | `guidance` |
| Spatial assistance | `backend/app/navigation/surroundings.py`, tool `describe_surroundings` | always (needs a pose) |
| Hard constraints, location sources, pose | `backend/app/assistant/service.py` | always |
| Route path order fix (floor detours) | `backend/app/navigation/ported/challenge.py`, `response.py` | always |
| HQ outdated check | `frontend/src/lib/hqStale.ts`, `PipelineStatus.tsx` | always |
| Session memory | `backend/app/memory/`, `backend/app/api/routes/memory.py`, `frontend/src/lib/memory.ts`, `RecapCard.tsx` | `memory` (showcase) |
| Test/evidence tooling | `backend/scripts/nav_cases.py`, `dialog_scenarios_report.py`, `dialog_sim.py`; `scripts/e2e/*.mjs` | — |
| Media | `scripts/media/build_videos.py`, `build_slides.py`; `scripts/windows/Run-LiveDialogs.ps1` | — |

## Known limits

- Positions are simulated or stated; nothing is measured. Floor changes are drawn schematically.
- ElevenLabs: the quota ran out during the night media build; on 25.10. morning the raised key
  answered HTTP 200 again (`scripts/windows/Check-ElevenLabs.ps1`). The key lacks the `user_read`
  permission, so the remaining character count cannot be read through the API. When the quota is
  used up again, the app reports "Spoken hints paused" and keeps navigating.
- Rebuild starts from the dataset file: an asset moved only in this session keeps HQ marked
  outdated (honest, but not "fixed" by Rebuild).
- Precision routing is not available for every data family.
- Software-rendered frame times only; the campus HQ model has ~1 800 draw calls.

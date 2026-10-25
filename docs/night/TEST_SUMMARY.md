# Night build — test summary (2026-10-25)

Branch `noah/night-immersive` (from `86d0ca3` = main = Phase B). All numbers are from runs of
this branch tonight; evidence files are linked. Nothing below was measured on a physical phone.

| Area | Result | Evidence / command |
|---|---|---|
| Official deterministic harness (kit 1.1.0), baseline and final | **655/655 · 1338/1338 · 553/553 · 239/239** (unchanged) | `tests/nav_benchmark_final.md` · `backend: .venv/Scripts/python scripts/nav_benchmark.py` |
| Seeded navigation cases, 5 building families, independent path checks | **140/140** (88 reachable, 52 blocked/unsupported); seeds 1–3 × 200: **600/600** | `tests/nav_cases.md` · `scripts/nav_cases.py` |
| Movement cases on real buildings (walls, wide doors, rides, frame drops) | **54/54**; stress ×25: **990/990** | `frontend/src/lib/walkable.cases.test.ts` |
| Offline dialog/state/tool scenarios (scripted tool calls, real assistant + router; not NLU) | **60/60** (found 6 defects, all fixed) | `tests/dialog_scenarios_offline.md` |
| Live Claude dialogs (Sonnet 5.5, medium), 20 dialogs / 31 turns, 7 held out | **19/20** twice (v1 before, v2 after the fixes); held-out 7/7 on first run; the one miss is a 4-sentence answer (limit 3) | `../reports/dialog_sim_night_live_v1.md`, `_v2.md` · $0.30 each |
| Backend unit/integration | **444 passed**, 4 skipped, 3 deselected (live) | `backend: .venv/Scripts/python -m pytest -q` |
| Frontend unit | **173 passed** (17 files); `tsc` clean; `astro check` 0 errors | `frontend: npx vitest run` |
| Browser: immersive (POV, WASD/drag, collision, one driver, off-route replan, split view, orientation events, phone/landscape/1000×860 centre coverage, core profile) | **14/14** | `e2e/immersive_e2e.json` · `scripts/e2e/immersive_e2e.mjs` |
| Browser: Phase B guidance regression | **14/14** | `scripts/e2e/guidance_e2e.mjs` |
| Browser: Phase A regression (offline backend) | **10/10** | `scripts/e2e/browser_e2e.mjs` |
| Browser: HQ showcase (3 buildings from saved model, second open reuses file, explicit Rebuild runs, moved chair → honest LQ, frame time) | **8/8** | `e2e/hq_e2e.json` · `scripts/e2e/hq_e2e.mjs` |
| Full visual scenes against the LIVE app (recorded) | **3/3**: coffee → route → walk → arrival; wheelchair + closed lift → no stairs → alternative; walk yourself → "what's on my right" | `scenes/*.png`, `scenes/scenes_log.json` |
| Session memory / recap (showcase, opt-in, demo labelled synthetic) | backend 25 tests; browser recap after preview + walk: preview not counted, 21.4 m walked | `e2e/H01-recap-1280.png` |

## Render performance (measured, software GL only)

| Context | Median frame | p95 | Draw calls | Triangles |
|---|---|---|---|---|
| Headless Chrome, SwiftShader, 1440×900, HQ factory overview | 43.6 ms | 48.4 ms | 753 | 13.8 k |
| 390×844 DPR 2 emulation, CPU ×4 throttled, SwiftShader, HQ campus | 256 ms | 268 ms | 1 779 | 144.6 k |

SwiftShader is a CPU renderer; these numbers are a lower bound, not a phone measurement. The
campus HQ model has many draw calls (instancing would be the first optimisation).

## Visual review (screenshots opened and looked at)

Every screenshot listed in the guide was opened and inspected. Real defects found only that way
and fixed tonight: guidance card covering the POV on phones (100 %/67 %/40 % centre covered → 0 %),
HQ walls squashed to ~1 m in an 8 m hall storey (looked over every wall in first person), room
highlight tinting the first-person view, raw element id in a router note, HUD hidden behind the
compact chat card, split view half hidden behind the docked panel, desktop touch joystick.

## Not tested

Physical smartphone (orientation permission, calibration), real microphone, audio playback in a
real browser, multi-user use. Device-orientation checks used synthetic `DeviceOrientationEvent`s.

# AriNav Phase B — interactive 3D navigation

Branch `noah/phase-b-navigation` (from team `main` 6884927). Not merged into `main`.

## Start

```powershell
# offline (no AI, no voice): everything except chat and spoken hints
powershell -File scripts/windows/Start-AriNav.ps1 -DatasetsDir <folder with building datasets>
# live: Claude assistant + ElevenLabs voice (keys registered once with Register-ApiKey.ps1)
powershell -File scripts/windows/Start-AriNav.ps1 -Live -DatasetsDir <folder>
```

Open http://localhost:4321 and pick a building under **Location → Building**. Docker/native start
as in `README.md` also works (keys in `backend/.env`).

## How to navigate

1. **Find the destination** — ask the assistant ("I'm in Office 1.04, take me to the nearest exit")
   or, in **Developer** mode, set start and target by search or map pick and press *Compute route*.
2. **See the whole route** — it is drawn on the 3D map (start, destination, doors, stairs/lifts;
   floor changes without real 3D geometry are labelled *schematic*). The navigation card (top
   right) shows the destination and the next instruction.
3. Optional: **Preview route** flies the camera along the route; your position does not change.
   *Skip preview* stops it at any time.
4. **Start navigation** — a person walks along the backend's waypoints. Controls: *Pause*,
   *Resume*, *Stop*, *Overview* (whole building), *Recenter* (follow the person), walking speed
   0.5×–8×. The floor the person is on is focused automatically; the walked part of the route turns
   grey.
5. **Arrive** — the person stops at the actual target point (an exit route ends at the exit door).

**Voice:** short hints at doors, turns, floor changes and arrival (ElevenLabs, default 1.10×).
*Voice on/off*, *Repeat* and a speed slider are on the card. Hints never overlap; floor changes and
arrival are not crowded out at high speed; route changes cancel stale audio; push-to-talk stops all
playback first. If voice fails, navigation continues and the card says so.

**Closures while walking:** when the shared scenario changes (a confirmed closure, the developer
panel, another tab, the team's navgraph agent), the person stops, the route is recomputed from the
exact current position and walking continues only with a valid route. If nothing is reachable the
person stays put and the card explains why (names, no ids). The scenario is polled every 2 s while
navigating.

**User vs. developer view:** both show the same simulation (same position and scenario). The
developer view adds floor, position, heading, progress, waypoint, route/scenario revisions and
simulation time.

**HQ models:** opening a building never starts the paid pipeline (Developer → *HQ model* →
auto-build is off by default). A model stored under the building id
(`backend/data/workspace/locations/<building_id>/3d/model.glb`, gitignored) loads directly; the
*LQ only · Build HQ* chip or *Rebuild* regenerate it on request. Scenario changes never regenerate
the model; closures are painted on it.

## What was built

| Part | Where |
|---|---|
| Instructions + deterministic walker (`GuidanceSim`: position = route at integrated sim time) | `frontend/src/lib/simulation.ts` |
| Guidance hook (animation loop, pose store, reroute with stale-answer guard, dev test clock) | `frontend/src/components/arinav/useGuidance.ts` |
| Navigation card (user/developer view) | `GuidancePanel.tsx`, styles in `arinav-phase-a.css` |
| Walker figure, follow camera, overview, route preview, walked-route overlay | `scene.ts`, `Viewer3D.tsx` |
| Spoken hints (queue, priority, mute, repeat, cache) + `POST /api/assistant/speak` | `lib/guidanceVoice.ts`, `lib/voice.ts`, `backend/app/api/routes/assistant.py` |
| Reroute from the exact position (`exact_start`, official routes only, opt-in) | `backend/app/navigation/service.py` |
| HQ model loaded from storage without a run record | `usePipeline.ts` |

Phase A fixes (B0) for the QA findings D1–D3, N01–N03, U1, U2 are in commit a5de921.

## Tests (25.10.2026, this branch)

| What | Result |
|---|---|
| Backend `uv run pytest` | 349 passed, 4 skipped |
| Kit 1.1.0 harness `scripts/nav_benchmark.py` | see `docs/reports/nav_benchmark_1_1_0.md` (655/655, 1338/1338, 553/553, 239/239) |
| Frontend `npm test` / `npm run lint` / build | 72 tests, 0 errors |
| QA scripts of the independent review, rerun on this code | dialog audit 20/20, N01–N03 repros fixed |
| Browser acceptance `scripts/e2e/guidance_e2e.mjs` (controlled sim clock) | 14/14 → `docs/reports/browser-b/guidance_e2e.json` |
| Phase A browser suite `scripts/e2e/browser_e2e.mjs` | 10/10 |
| Live: assistant → route → navigation → ElevenLabs hints | arrived at the exit door; 7 hints at 1.1×, no overlap, no errors |

The browser checks cover: preview keeps the position; route/endpoints equal the backend's; exit
routes end at the exit door; the precision walker never leaves the checked polyline; pause/resume
without jumps; closures reroute from the exact position; no route stops the walker; floor order;
official vs. precision stay distinguishable; audio failures do not block; missing HQ does not block;
user and developer view show the same position; two sessions keep separate positions; a cached HQ
model loads without a pipeline start. Stale async answers are covered by `useGuidance.test.ts`.

## Limits

- The walker follows route waypoints at constant speed; vertical moves take their official cost
  equivalent (a lift ride counts 15 m + height). No real indoor positioning yet.
- Official routes ignore furniture by kit rule; only precision routes are obstacle-checked.
- `exact_start` re-walks only the first leg inside the current room; the reported official cost
  remains the kit's value from the room.
- The HQ model is baked: navgraph asset edits update the LQ model and all routers, the HQ GLB keeps
  the old furniture until *Rebuild*.
- The team pipeline still skips an explicitly started run whose raw input is unchanged (SHA-256);
  opening and loading never hash.
- Real microphone test by a person is still open.

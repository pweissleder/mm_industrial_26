# Core-final fixes on Pascal's UI (2026-10-25)

Branch `noah/core-final`, based on `origin/main` `762b723` (Pascal's merged UI, contains the night
branch up to `3694522`). No change to `main`, no merge.

## What changed

| Area | Before (reproduced on `762b723`) | Now |
|---|---|---|
| Door/entrance search | "Entrance 15" found nothing (the details card says "Entrance O0015"; search only knew room names); "I'm at …" diluted the query | numbers of doors/entrances (`Entrance 15`, `entrance15`, `door 015`, `Entrance O0015`) and filler words of position phrases are understood; rows carry a `label` like the card |
| Width | width was only used by the precision router; the assistant said width checks were unsupported | official routes take `min_door_width_m`: doors narrower than the stated width (+ 2 × stated margin) are avoided, an alternative is taken when one exists, otherwise the narrow door is named (`diagnosis.category = too_narrow`). `width_check` says what was covered: **doors only — corridors, furniture and lifts are not width-checked**; doors of unknown width are listed, never guaranteed. The assistant keeps the stated width for follow-ups; the UI, reroutes and the assistant send the same constraint. Without the field nothing changes (kit benchmark identical) |
| Equipment as start | the official start of an item is the item's centre (CNC mill: 0.65 m from its centre, inside its 3.0 × 2.5 m body); manual walking started at the room centre | `beside_asset`: the route starts at the reachable point just outside the footprint with the shortest official walk (2.3 m from the centre); official costs unchanged. Manual walking from equipment starts beside it |
| Arrival | "You have arrived" but the route, card and directions stayed | green check "You've arrived!", then the navigation closes once (route and directions cleared); the person stays at the target, which is the start of the next route. Users without positioning can say "I've arrived" in the chat; the user/navigator card says the walk is simulated |
| Phone rotation | only the compass angle (yaw) was used; 72° vertical view looked zoomed on a portrait phone | yaw, pitch and roll from alpha/beta/gamma (`deviceLook`): ceiling and floor, no flip near straight up/down, no jump at 0/360°, landscape aware; about 70° across on portrait phones. Rotation changes only the view, never the position |
| Desktop developer view | first person as a 50/50 split | the overview fills the screen; first person is an embedded phone (same pose, route and heading); WASD and speed kept |
| Phone developer view | — | one full-screen view (no split, no phone mock-up), verified at 390 × 844 |
| Objects in first person | clicks went to the drag-to-look layer and the orbit camera; cards were hidden while navigating | a click/tap without dragging picks through the eye camera; walls stop the pick; equipment, doors, lifts and stairs open their card in first person |
| View switch | full segmented control on desktop, folded only on phones | folded to the current view's icon everywhere; hover/click/tap unfolds, a choice, Escape or a click outside folds it; same state as before (no second view state) |
| Fly-through | — | already removed from the navigation card by Pascal; no entry point left |
| Simulated person | orange cylinder + ball scaled ×1.6 (about 2.8 m), unlit, drawn over every wall | a worker at real scale (about 1.75 m): white hard hat, orange high-visibility vest with reflective bands, dark trousers, lit like the building; legs and arms swing with the distance walked; a blue position disc and view beam on the floor stay visible and grow with camera distance; parts hidden by walls show as a soft navy silhouette (`walker-*-1440.png`, `scripts/e2e/walker_shots.mjs`) |
| Precision router | — | works on the factory with all three profiles (9/9 routes obstacle-checked); developer labels now "Machines & fixed equipment / All obstacles / Walls only". A failed precision route is reported, never replaced silently by the official one |

## Follow-up from the phone test (11:30)

| Reported | Now |
|---|---|
| A clicked thing offered no "I'm here" (navigator profile; cards hidden while navigating) | every profile: the card opens (also while navigating) with "I'm here" / "Take me there" (developer: From/To here). "I'm here" works for rooms, equipment and doors and replaces where the person walked; in first person they stand there |
| Navigation card covers the phone screen, cannot be made smaller | chevron folds it to the next step while navigating (phones start folded: 120 px instead of 269 px; in first person only the title row, 52 px) |
| No WASD in the user profile; stuck on the phone | walking yourself in every profile: WASD on desktop, a small 84 px stick on touch screens |
| No button to walk the route automatically | "Auto-walk" in the mode bar hands the person back to the route walker from where they stand |
| Arrival left first person | after the check the person stays in first person at the target and can walk on |
| Tools on the right without names | name next to each camera tool on hover |
| View switch moved under the pointer | a small menu under the view icon; nothing shifts; hover/click/tap opens, a choice, Escape or a click outside closes |

Browser: `scripts/e2e/user_fix_check.mjs` 6/6 (phone 390 × 844 and desktop, no assistant calls),
`core_final_check.mjs` 7/7.

## Evidence

| Check | Result |
|---|---|
| Backend `pytest` | 580 passed, 4 skipped, 3 deselected (new: `tests/navigation/test_width_routing.py`, width integration test in `tests/assistant/test_assistant_integration.py`) |
| Official kit 1.1.0 harness `scripts/nav_benchmark.py` | **655/655 · 1338/1338 · 553/553 · 239/239** (unchanged) |
| Frontend `vitest` / `tsc` / `astro check` | 210 passed / clean / 0 errors (new: `deviceLook` tests in `src/lib/spatial.test.ts`) |
| Browser `scripts/e2e/core_final_check.mjs` (offline backend, no LLM) | 7/7 — equipment start beside the mill, arrival check then close, new route starts at the target, view switch fold/hover/Escape, desktop developer phone + click on a meeting table in first person, phone 390 × 844 single view, synthetic 3-axis rotation while the walk continues |
| Browser `scripts/e2e/hq_check.mjs` | factory, campus 001 and swiss 1689: HQ shown, Preview (standard) switch works, back to HQ |

Screenshots: `docs/core-final/e2e/*.png` (looked at; not proof by existence).

## Not covered

- Physical phone: orientation permission (iOS), the real feel of 3D rotation, tapping things in
  first person on a touch screen (same code path as the desktop click, not separately proven).
- The older night E2E scripts (`immersive_e2e`, `guidance_e2e`, `hq_e2e`) still look for the old
  mode buttons and the removed fly-through; they were not adapted to Pascal's UI.
- No progress slider in the walkthrough; floor-change transitions unchanged from the night build.
- HQ models of campus 001 and swiss 1689 are local pipeline outputs (git-ignored); a fresh checkout
  shows "LOW · BUILD HQ" for them until they are built.
- Live assistant dialogs were not re-run (no paid calls); the width behaviour is covered by the
  scripted offline integration test.

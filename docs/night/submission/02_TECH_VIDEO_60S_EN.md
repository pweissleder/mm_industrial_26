# AriNav — Technical explanation draft (60 seconds)

Suggested voiceover (~116 words; measure with the actual voice):

“AriNav separates conversation from computation. Structured building data provides rooms, openings, floors and assets. A shared service supplies the map, manual controls and assistant with the same scenario. Claude resolves the user's intent and calls validated tools; it does not invent route geometry. The official routing mode follows the Synera reference rules. An additional precision mode checks supported obstacle geometry and user clearance. ElevenLabs handles speech input and output. Our simulator reuses the actual route, while camera position remains separate from user position. We test geometry, state changes, reference answers and browser behavior independently. Real device localization remains separate from simulated movement. The result is a modular prototype that works through both conversation and direct controls.”

Shots: actual architecture diagram → input JSON and deterministic route → real tool/result/map → same path from two camera perspectives → measured test summary → honest limitations.

Before recording, confirm “shared service”, module support and tests on the final integration. Add exact counts only as current dated captions, with structured-vs-language scope clearly stated. Do not use simulated orientation tests as proof of real smartphone tracking.

## Final cut (night build) — `arinav_tech_60s.mp4`

Built by `scripts/media/build_videos.py` from real app recordings/screenshots; file outside Git in `HackathonTransfer/Night_2026-10-25/media/`. Narration as subtitled:

- AriNav keeps one building and one scenario as the single source of truth for the map, the router and the assistant.
- A pipeline validates the data and builds a room graph and a 3D model, stored by building id. Rebuild always runs, and a moved thing marks the high quality view as outdated.
- Routes come from the official kit router. The harness still scores six hundred fifty five out of six hundred fifty five, and a hundred forty seeded cases check every path independently.
- Claude calls typed tools. Hard guards keep wheelchair constraints binding, and side questions are answered from geometry. Sixty offline scenarios and twenty live dialogs test it.
- The client adds a first person view, walking with collision, and phone rotation behind a permission. Three profiles switch modules, so the core always works alone.
- Limits stay explicit: positions are simulated or stated, and real phone sensors are not tested yet.

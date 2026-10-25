# AriNav — Technical explanation draft (60 seconds)

Suggested voiceover (~116 words; measure with the actual voice):

“AriNav separates conversation from computation. Structured building data provides rooms, openings, floors and assets. A shared service supplies the map, manual controls and assistant with the same scenario. Claude resolves the user's intent and calls validated tools; it does not invent route geometry. The official routing mode follows the Synera reference rules. An additional precision mode checks supported obstacle geometry and user clearance. ElevenLabs handles speech input and output. Our simulator reuses the actual route, while camera position remains separate from user position. We test geometry, state changes, reference answers and browser behavior independently. Real device localization remains separate from simulated movement. The result is a modular prototype that works through both conversation and direct controls.”

Shots: actual architecture diagram → input JSON and deterministic route → real tool/result/map → same path from two camera perspectives → measured test summary → honest limitations.

Before recording, confirm “shared service”, module support and tests on the final integration. Add exact counts only as current dated captions, with structured-vs-language scope clearly stated. Do not use simulated orientation tests as proof of real smartphone tracking.

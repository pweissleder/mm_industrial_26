# AriNav — Architecture and Integration Notes
**Draft; finalize against the actual night-build commit.**

## Purpose
A conversational indoor-navigation prototype that combines structured building data, deterministic tools and an interactive map. The assistant handles questions, clarification and tool selection. Geometry and route validity remain in code.

## System boundaries
| Layer | Responsibility | Verify before submission |
|---|---|---|
|Building repository|Load shared rooms, openings, floors and assets|Current ingestion path and supported data|
|Navigation service|Official reference mode; separate precision mode|Current API, exact endpoint handling, supported geometry|
|Scenario state|Closures and confirmed edits with revision handling|UI/assistant share current state|
|Assistant|Intent, dialogue context, validated tool calls|Actual model/config; no unconfirmed edits|
|Voice|ElevenLabs transcription and speech playback|Working live entrypoint, key storage, failure fallback|
|AriNav renderer|Map, route, selection and optional POV|Real renderer stack, mobile limits|
|Simulation|Controlled user pose; camera independent|Exact-vs-schematic transitions; deterministic tests|
|Optional history|Opt-in local events and recap|Deletion/reset and no raw audio by default|

## Data and routing
The Synera kit supplies structured JSON, equipment tables and visualization files. Kit1.1.0 changes expected route answers and routing conventions; pin the actual reference commit used. Official routing ignores furniture. Precision routing uses selected footprints and declared clearance and is not available for every data source. Do not present benchmark cost as physical walking distance or real travel time.

The virtual camera and user position are separate. Device rotation may change heading after calibration; it does not independently determine a real indoor location. Synthetic camera-localization experiments, if any, must be identified separately.

## Verification summary — fill from final run
- Code commit/branch: [VERIFY]
- Reference/data version: [VERIFY]
- Structured benchmark: [CURRENT RESULT; previous claim655/655]
- Warm-up routes / locate / count: [CURRENT RESULT; prior1338/1338,553/553,239/239]
- Browser/visual/negative scenarios: [CURRENT RESULT]
- Natural-language live cases and budget: [CURRENT RESULT; not equivalent to full benchmark]
- Physical phone/microphone tests: [VERIFIED / NOT TESTED]
- Known unsupported geometry or features: [LIST]

## Security and privacy
API keys remain server-side/local secure storage. No public raw transcripts or audio. User/location memory is optional, local and deletable. Confirmation required for scenario writes; constraint changes are explicit. No certified accessibility or emergency-routing guarantee.

## Reproduction
[Actual start commands, Python/Node versions, environment variable names WITHOUT secrets, data placement, module presets, test commands.]

## Limitations
[Keep actual limitations: unavailable devices, schematically modeled floor changes, source data gaps, unsupported precision families, reference-vs-product endpoints. Remove only when genuinely resolved.]

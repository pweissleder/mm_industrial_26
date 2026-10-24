# Official kit v1.1.0 (2026-10-24) — impact on this prototype

Reference: starter kit branch `patch/fix-answers-ruleset`, commit `6a2cdec` (CHANGELOG 1.1.0).
Buildings, equipment and question wording are unchanged; **expected route answers** follow the
judges' rules: door-to-door walking on `polygon.buffer(-0.15)` with a visibility graph (segment clear
if `buffer(0.02)` of the shrunken outline covers it), an opening lands 30 cm inside each space
(nearest point of `buffer(-0.30)`, step added), lengths rounded to 3 decimals, stairs 2 m per metre
climbed, lift 15 m once per ride + 1 m per metre, every pair of lift stops joined, no outside link,
routes to an item end in the space that holds it.

This prototype still pins kit **1.0.0** (`data.py` checks the reference module against the ZIP).
No router was rewritten for 1.1.0.

## Measured against v1.1.0 dev answers (offline, deterministic)

| Area | Result vs 1.1.0 | Cause |
|---|---|---|
| Benchmark operations (`operations.py`, starter `BuildingMap` 1.0.0) | 121/138 match. All of `largest_room`, `floor_area_total`, `wide_delivery_count`, `step_free_reachable_count`, `room_stacked`, `shared_walls_count`, `straight_line_3d` match; **`errand` 3/19, `what_if_door` 0/2** | These two use route lengths from the old anchor / 12 m / 15+6 m / outside model |
| `challenge.py` on warm-up routes (production rule off) | 161 expected routes: same `space_path` 144, cost within 1 % **62**, within 5 % 148 (median error 1.2 %); 28 expected `reachable:false`: agree 21 | Door crossing differs (stub to the 0.15 m outline vs. official 30 cm landing), 2 cm visibility tolerance, rounding; a few unreachable disagreements unexplained yet |
| Production-transit / forklift rules in `challenge.py` | Not part of 1.1.0; they changed 0 of the dev warm-up routes but block ~29 % of random factory pairs | Came from organizer remarks, not from the published kit — team decision needed |

## Tests that must be re-checked (or re-pinned) for 1.1.0

- `test_operations.py`: the Dev 138/138 assertion (currently against 1.0.0 answers) — with 1.1.0
  answers `errand` and `what_if_door` fail until they use the 1.1.0 route model.
- `test_example_lab.py`, `test_service.py`: reference route / example checks and the source-drift pin
  (`data.reference_module` compares `building_map.py` with the 1.0.0 ZIP and will raise
  `source_drift` once the checkout is moved to 1.1.0).
- `test_navigation.py`, `test_portal_usability.py`, `test_http.py`: assertions on
  `reference_comparison` / `model='reference'` costs (12 m per floor, 15+6 m lift, outside node).
- `test_challenge.py`: door-crossing geometry, asset endpoints (1.1.0 ends in the holding space),
  production-transit default, and a new comparison test against 1.1.0 `questions_basic` routes.
- `test_assistant.py` test_10 (largest_room) is unaffected; challenge-mode live checks should be re-run.
- Historical Train 517/517 oracle evidence is 1.0.0-only.

Precision routing (`navigation.py`) has its own physical contract and is not graded by these rules.

## Suggested minimal next step (not done)

Switch `data.py` to the 1.1.0 kit, adopt the official door landing and tolerances in `challenge.py`
(or call 1.1.0 `building_map.py` for graded answers), make the production/forklift rules opt-in, and
add the v1.1.0 comparison above as a regression test with a 1 % threshold.

# Official kit v1.1.0 (2026-10-24) — status in this prototype

**Done: the prototype is pinned to kit 1.1.0 and reproduces every Train/Dev answer.**
Reference: starter kit `main` at commit `dc03c447dbe5b2e5f82c7db0d72249931817d86d` (CHANGELOG 1.1.0).
Kit 1.1.0 changed only expected answers, `starter_agent/building_map.py` (route rules), `evaluate.py`,
`viewer.py`, README section 3 and the CHANGELOG; buildings, images, manifest and questions are unchanged.

## Data pin (`data.py`)

- `KIT_COMMIT` = `dc03c44…`. `pinned_tree()` reads the commit's tree (`git ls-tree`, no network, lazy fetch
  disabled) from the reference checkout (`SYNERA_REFERENCE_DIR`).
- `reference_module('building_map' | 'score')` loads the checkout's file only if its git blob id
  (CRLF-normalised) equals the commit's; the two blob ids are also pinned in code.
- Buildings still come from the v1.0.0 ZIP (`SYNERA_KIT_ZIP`). Every `building.json` loaded is checked
  against the 1.1.0 blob id; all 240 Train/Dev buildings are identical (`verify_buildings()`).
- `examples(split)` / `basic_examples(split)` read `questions.jsonl` / `questions_basic.jsonl` of 1.1.0, each
  verified against the commit (checkout file, or the ZIP member when it is byte-identical).
- Reference checkout setup: `git merge --ff-only origin/main`, then non-cone sparse patterns
  `/* !/*/ /benchmark/ /starter_agent/ /data/{train,dev}/*/questions.jsonl /data/{train,dev}/*/questions_basic.jsonl`.
  The 115 question blobs unchanged since 1.0.0 were missing in the partial clone; they were written into the
  object store from the ZIP after checking that each hashes to the commit's blob id (no fetch).

## Routing

- **Challenge mode = official router.** `challenge.official_route` builds the official 1.1.0 `BuildingMap`
  graph and search; cost, space_path, reachability and `directions` are the official ones and are cross-checked
  against `BuildingMap.route` on every call. Endpoints resolve like `resolve_space` (an asset ends in its
  holding space). Drawable segments replay the official walk (door → 30 cm landing → visibility path on
  `buffer(-0.15)` with 0.02 cover tolerance → landing → door); each drawn edge equals its official weight.
- **Production-transit and forklift rules:** not in kit 1.1.0 (README, CHANGELOG and starter code mention
  forklifts only as an optional level-5 idea). They are opt-in request flags `no_production_transit` and
  `forklift` (default off), labelled "unofficial (organizer remark, not in kit 1.1.0)".
- `model='reference'` is now the official 1.1.0 graph drawn as straight node-to-node lines (cost = official
  length); precision results keep `reference_comparison`, now on the 1.1.0 rule-based cost.
- Benchmark operations (`errand`, `what_if_door`, `wide_delivery_count`, `step_free_reachable_count`) use the
  official 1.1.0 graph; `what_if_door` adds the door with the official `add_door(position=…)` at the middle of
  the shared wall; "walk out" = route length to the nearest entrance opening (no outside link).

## Measured (`benchmark_eval.py`, offline, deterministic; reports in `reports/`)

| | Before (commit 175542d: kit 1.0.0 `BuildingMap` + old `challenge.py`) | After |
|---|---|---|
| Benchmark, official scorer | 568/655 (dev 120/138, train 448/517); errand 10/93, what_if_door 0/4, other kinds 100 % | **655/655** (dev 138, train 517) |
| Warm-up locate / count (deterministic lookup) | 553/553 / 239/239 | 553/553 / 239/239 |
| Warm-up routes incl. tours (1,338; 1,105 reachable) | reachability 1337/1338; space_path 1102/1105; element_path 1102/1105; length ≤1 % 335/1105; ≤0.01 m 11/1105 | **all 1338 exact**: reachability, space_path, element_path, length ≤0.01 m |

Tours: every leg (208/208) matches to the centimetre; totals are equal to 2 decimals in 144/208. A tour
answer is the sum of its rounded legs, while the official `BuildingMap.route(via=…)` rounds the total once,
so 64 totals differ by exactly 0.01 m (inside the 0.01 m check and far inside the scorer's ±1 %).
Drawn segments equal the official edge weight (±0.6 mm) on all 1,105 reachable routes; 7 routes contain an
edge where the official model has no clear walk and uses 1.5 × the straight distance (drawn straight, warned).
Harness runtime: ~89 s for train+dev (benchmark 57 s, warm-up 32 s); full test suite 131 tests, ~60 s.

Earlier ad-hoc dev counts (121/138 ops; 144/161 space paths; 62/161 lengths ≤1 %; 21/28 unreachable agree)
were reproduced and corrected: the ad-hoc check passed `step_free` but **dropped `closed_element_id`**, so
closure routes were computed without their closure. With closures applied the old code gives 160/161 space
paths, 73/161 lengths ≤1 % (157 ≤5 %, median error 1.06 %) and 28/28 unreachable agreement. 121/138 was an
arithmetic slip: its own per-kind numbers (errand 3/19, what_if_door 0/2) give 120/138, which the harness confirms.
The production rule changed none of these results.

## Known limits

- `osm-way360544293` needs ~22–28 s for the first official graph (the official per-space Floyd-Warshall on
  its large halls); later routes take ~35 ms. Other buildings take < 1 s.
- `README.md` (not edited here) still describes the 1.0.0 pin and the old test count.

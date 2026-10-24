# Benchmark harness vs kit 1.1.0 answers: before (code at 175542d, kit 1.0.0 BuildingMap + old challenge.py, production rule default on)

Code pinned to kit 1.0.0 (commit `cb51a6f2e357db743b0c8b2b377fe902ee3ced13`); questions from the starter checkout (SYNERA_REFERENCE_DIR)/data. Offline, deterministic, no model calls. Runtime 23.5 s (benchmark 9.2 s, warm-up 14.3 s). Splits: train, dev.

## Benchmark questions (official scorer)

Overall **568/655**; dev 120/138, train 448/517

| Kind | Correct |
|---|---|
| errand | 10/93 |
| floor_area_total | 181/181 |
| largest_room | 96/96 |
| room_stacked | 26/26 |
| shared_walls_count | 143/143 |
| step_free_reachable_count | 2/2 |
| straight_line_3d | 30/30 |
| what_if_door | 0/4 |
| wide_delivery_count | 80/80 |

By source: osm_indoor 71/82, procedural 131/152, resplan 175/188, swiss_dwellings 191/233

## Warm-up questions

| Kind | Correct | Reachability agrees | space_path exact | element_path exact | length ≤1 % | length ≤0.01 m |
|---|---|---|---|---|---|---|
| locate | 553/553 | – | – | – | – | – |
| count | 239/239 | – | – | – | – | – |
| route | 67/720 | 719/720 | 657/660 | 657/660 | 185/660 | 7/660 |
| route_step_free | 76/240 | 240/240 | 168/168 | 168/168 | 68/168 | 4/168 |
| route_with_closure | 69/138 | 138/138 | 69/69 | 69/69 | 39/69 | 0/69 |
| tour | 32/240 | 240/240 | 208/208 | 208/208 | 43/208 | 0/208 |
| routes_without_tours | 212/1098 | 1097/1098 | 894/897 | 894/897 | 292/897 | 11/897 |
| all_routes | 244/1338 | 1337/1338 | 1102/1105 | 1102/1105 | 335/1105 | 11/1105 |

Route rows: "Correct" = reachability agrees and, if reachable, identical space_path and element_path and length within 0.01 m. Path/length columns count against expected-reachable questions.
Exact to the centimetre (2 decimals): routes without tours 7/897; tours: every leg 0/208, total 0/208 (a tour answer sums the rounded legs, the official route() rounds the total once, so totals may differ by 0.01 m).
Drawn segments equal the official walk edge lengths (±0.6 mm) on 0/0 reachable routes; routes with a blocked-walk fallback edge (1.5 × straight, drawn straight): 0.

Routes by source: osm_indoor 21/72, procedural 0/210, resplan 8/475, swiss_dwellings 215/581

## Failures by cause

| Section:kind:cause | Count |
|---|---|
| warmup:route:length_off_over_1pct | 472 |
| warmup:route:length_off_under_1pct | 178 |
| warmup:tour:length_off_over_1pct | 165 |
| warmup:route_step_free:length_off_over_1pct | 100 |
| benchmark:errand:value_off | 83 |
| warmup:route_step_free:length_off_under_1pct | 64 |
| warmup:tour:length_off_under_1pct | 43 |
| warmup:route_with_closure:length_off_under_1pct | 39 |
| warmup:route_with_closure:length_off_over_1pct | 30 |
| benchmark:what_if_door:count_off | 4 |
| warmup:route:different_space_path | 2 |
| warmup:route:expected_route_got_unreachable | 1 |

By source: warmup:resplan 467, warmup:swiss_dwellings 366, warmup:procedural 210, warmup:osm_indoor 51, benchmark:swiss_dwellings 42, benchmark:procedural 21, benchmark:resplan 13, benchmark:osm_indoor 11

### First failures

- `osm-way13507441-H03` (errand, osm_indoor): value_off
- `osm-way50299720-H03` (errand, osm_indoor): value_off
- `osm-way58216509-H04` (errand, osm_indoor): value_off
- `osm-way58216509-H07` (what_if_door, osm_indoor): count_off
- `osm-way58216509-H08` (what_if_door, osm_indoor): count_off
- `osm-way61408163-H03` (errand, osm_indoor): value_off
- `proc-campus-train-000-H04` (errand, procedural): value_off
- `proc-campus-train-001-H04` (errand, procedural): value_off
- `proc-campus-train-002-H04` (errand, procedural): value_off
- `proc-campus-train-004-H04` (errand, procedural): value_off
- `proc-campus-train-011-H04` (errand, procedural): value_off
- `proc-campus-train-014-H04` (errand, procedural): value_off
- `proc-campus-train-019-H04` (errand, procedural): value_off
- `proc-factory-train-005-H04` (errand, procedural): value_off
- `proc-factory-train-007-H04` (errand, procedural): value_off
- `proc-factory-train-009-H04` (errand, procedural): value_off
- `proc-factory-train-015-H04` (errand, procedural): value_off
- `resplan-120-H04` (errand, resplan): value_off
- `resplan-12985-H03` (errand, resplan): value_off
- `resplan-13024-H03` (errand, resplan): value_off
- `resplan-14602-H04` (errand, resplan): value_off
- `resplan-16484-H04` (errand, resplan): value_off
- `resplan-2515-H03` (errand, resplan): value_off
- `resplan-3221-H03` (errand, resplan): value_off
- `resplan-47-H03` (errand, resplan): value_off

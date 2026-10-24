# Benchmark harness vs kit 1.1.0 answers: after (official kit 1.1.0 router)

Code pinned to kit 1.1.0 (commit `dc03c447dbe5b2e5f82c7db0d72249931817d86d`); questions from data layer (verified). Offline, deterministic, no model calls. Runtime 89.3 s (benchmark 57.4 s, warm-up 31.9 s). Splits: train, dev.

## Benchmark questions (official scorer)

Overall **655/655**; dev 138/138, train 517/517

| Kind | Correct |
|---|---|
| errand | 93/93 |
| floor_area_total | 181/181 |
| largest_room | 96/96 |
| room_stacked | 26/26 |
| shared_walls_count | 143/143 |
| step_free_reachable_count | 2/2 |
| straight_line_3d | 30/30 |
| what_if_door | 4/4 |
| wide_delivery_count | 80/80 |

By source: osm_indoor 82/82, procedural 152/152, resplan 188/188, swiss_dwellings 233/233

## Warm-up questions

| Kind | Correct | Reachability agrees | space_path exact | element_path exact | length ≤1 % | length ≤0.01 m |
|---|---|---|---|---|---|---|
| locate | 553/553 | – | – | – | – | – |
| count | 239/239 | – | – | – | – | – |
| route | 720/720 | 720/720 | 660/660 | 660/660 | 660/660 | 660/660 |
| route_step_free | 240/240 | 240/240 | 168/168 | 168/168 | 168/168 | 168/168 |
| route_with_closure | 138/138 | 138/138 | 69/69 | 69/69 | 69/69 | 69/69 |
| tour | 240/240 | 240/240 | 208/208 | 208/208 | 208/208 | 208/208 |
| routes_without_tours | 1098/1098 | 1098/1098 | 897/897 | 897/897 | 897/897 | 897/897 |
| all_routes | 1338/1338 | 1338/1338 | 1105/1105 | 1105/1105 | 1105/1105 | 1105/1105 |

Route rows: "Correct" = reachability agrees and, if reachable, identical space_path and element_path and length within 0.01 m. Path/length columns count against expected-reachable questions.
Exact to the centimetre (2 decimals): routes without tours 897/897; tours: every leg 208/208, total 144/208 (a tour answer sums the rounded legs, the official route() rounds the total once, so totals may differ by 0.01 m).
Drawn segments equal the official walk edge lengths (±0.6 mm) on 1105/1105 reachable routes; routes with a blocked-walk fallback edge (1.5 × straight, drawn straight): 7.

Routes by source: osm_indoor 72/72, procedural 210/210, resplan 475/475, swiss_dwellings 581/581

## Failures by cause

None.

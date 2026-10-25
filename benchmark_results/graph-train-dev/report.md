# Graph-only benchmark (2026-10-25 04:34)

Data: /Users/pascal/Documents/Repositories/Privat/Hackathons/mm_industrial_26/problem_statement/industrial-agents-hackathon-2026/data/train, /Users/pascal/Documents/Repositories/Privat/Hackathons/mm_industrial_26/problem_statement/industrial-agents-hackathon-2026/data/dev. Answer model: `claude-haiku-5-5`. Time 1051 s, cost $8.39.

## Pipeline (graph mode: raw validation, graph build, graph validation)

**240/240 buildings passed** (completed without review, every stage passed, source geometry unchanged).


## Answers

2785 questions, 0 failed to run, 97 expected answers recomputed with the kit 1.1.0 route model.

| Group / kind | Correct | Score |
|---|---|---|
| **basic/ALL** | 2118/2130 (99%) | 0.994 |
| basic/count | 239/239 (100%) | 1.000 |
| basic/locate | 552/553 (100%) | 0.998 |
| basic/route | 715/720 (99%) | 0.993 |
| basic/route_step_free | 239/240 (100%) | 0.996 |
| basic/route_with_closure | 135/138 (98%) | 0.978 |
| basic/tour | 238/240 (99%) | 0.992 |
| **benchmark/ALL** | 650/655 (99%) | 0.992 |
| benchmark/errand | 92/93 (99%) | 0.989 |
| benchmark/floor_area_total | 181/181 (100%) | 1.000 |
| benchmark/largest_room | 96/96 (100%) | 1.000 |
| benchmark/room_stacked | 24/26 (92%) | 0.923 |
| benchmark/shared_walls_count | 142/143 (99%) | 0.993 |
| benchmark/step_free_reachable_count | 2/2 (100%) | 1.000 |
| benchmark/straight_line_3d | 29/30 (97%) | 0.967 |
| benchmark/what_if_door | 4/4 (100%) | 1.000 |
| benchmark/wide_delivery_count | 80/80 (100%) | 1.000 |

## Misses (17)

- osm-way37850565-H02 `room_stacked`: wrong
- osm-way37850565-H03 `room_stacked`: wrong
- osm-way37850565-H04 `shared_walls_count`: wrong
- osm-way37850565-Q05 `route`: route does not start, stop and end where asked
- osm-way37850565-Q06 `route`: route does not start, stop and end where asked
- osm-way37850565-Q07 `route`: route does not start, stop and end where asked
- osm-way37850565-Q08 `route_step_free`: route does not start, stop and end where asked
- osm-way37850565-Q09 `route_with_closure`: route does not start, stop and end where asked
- osm-way37850565-Q10 `tour`: route does not start, stop and end where asked
- osm-way52060470-H06 `straight_line_3d`: wrong
- osm-way58216509-H04 `errand`: wrong
- osm-way58216509-Q02 `route`: route does not start, stop and end where asked
- osm-way58216509-Q03 `route`: route does not start, stop and end where asked
- osm-way58216509-Q07 `tour`: route does not start, stop and end where asked
- proc-factory-train-006-Q09 `route_with_closure`: route does not start, stop and end where asked
- swiss-1650-Q01 `locate`: expected Corridor 0.07 (apartment 1)
- osm-way27917482-Q09 `route_with_closure`: route does not start, stop and end where asked

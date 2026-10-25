# Graph-only benchmark (2026-10-25 06:01)

Data: /Users/pascal/Documents/Repositories/Privat/Hackathons/mm_industrial_26/problem_statement/industrial-agents-hackathon-2026/data/train, /Users/pascal/Documents/Repositories/Privat/Hackathons/mm_industrial_26/problem_statement/industrial-agents-hackathon-2026/data/dev. Answer model: `claude-haiku-5-5`. Time 57 s, cost $0.06.

## Pipeline (graph mode: raw validation, graph build, graph validation)

**6/6 buildings passed** (completed without review, every stage passed, source geometry unchanged).


## Answers

17 questions, 0 failed to run, 0 expected answers recomputed with the kit 1.1.0 route model.

| Group / kind | Correct | Score |
|---|---|---|
| **basic/ALL** | 2/12 (17%) | 0.167 |
| basic/locate | 1/1 (100%) | 1.000 |
| basic/route | 0/5 (0%) | 0.000 |
| basic/route_step_free | 0/1 (0%) | 0.000 |
| basic/route_with_closure | 1/3 (33%) | 0.333 |
| basic/tour | 0/2 (0%) | 0.000 |
| **benchmark/ALL** | 1/5 (20%) | 0.200 |
| benchmark/errand | 1/1 (100%) | 1.000 |
| benchmark/room_stacked | 0/2 (0%) | 0.000 |
| benchmark/shared_walls_count | 0/1 (0%) | 0.000 |
| benchmark/straight_line_3d | 0/1 (0%) | 0.000 |

## Misses (14)

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
- osm-way58216509-Q02 `route`: route does not start, stop and end where asked
- osm-way58216509-Q03 `route`: route does not start, stop and end where asked
- osm-way58216509-Q07 `tour`: route does not start, stop and end where asked
- osm-way27917482-Q09 `route_with_closure`: route does not start, stop and end where asked

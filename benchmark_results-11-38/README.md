# Benchmark results (2026-10-24)

Data: `../industrial-agents-hackathon-2026/data` (train, dev, test splits). Model: `claude-opus-5-5`.

## Agent: the app's chat agent, scored with the official `benchmark.score`

| Split | Correct | Cost | Time |
|---|---|---|---|
| dev | 137 / 138 (99%) | $9.48 | 65s |
| train | 509 / 517 (98%) | $34.08 | 219s |
| test | 145 answered (no answer key) | $9.86 | 66s |

`agent/submission_test.csv` is the test submission. `*_results.jsonl` has every question, the expected answer and the reply. `*.log` has per-kind scores. `pilot/` is the first 9-question check.

Misses (9):
- 5 hedged on duplicate room names (osm-way37850565 H02/H03/H04 "Büro", osm-way52060470-H06 "Genusswerk", osm-way58216509-H04).
- 3 `shared_walls_count` one too low (proc-factory-dev-010-H05, proc-factory-train-007-H05, proc-factory-train-009-H05).
- 1 `wide_delivery_count` answered 0 instead of 3 (resplan-13846-H02).

## Solver: navgraph tools fed the question `inputs` directly

train 517 / 517, dev 138 / 138 (`solver/`). So every miss above is in the agent layer.

## Re-run

```
cd backend
uv run python navgraph_checks/bench_agent.py ../../industrial-agents-hackathon-2026 dev --out ../benchmark_results/agent
uv run python navgraph_checks/check_benchmark.py ../../industrial-agents-hackathon-2026/data/dev
```

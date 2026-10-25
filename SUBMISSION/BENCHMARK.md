# Benchmark

One command, no manual steps between input and output. Run it from the repo root, the folder that contains `SUBMISSION/` (needs `uv sync` and `ANTHROPIC_API_KEY` in `backend/.env`):

```bash
cd backend && uv run poe benchmark --data <folder with the building(s)> --submission ../SUBMISSION/answers.json
```

For the kit's test set:

```bash
cd backend && uv run poe benchmark --data ../problem_statement/industrial-agents-hackathon-2026/data/test --submission ../SUBMISSION/answers.json
```

`--submission` ends in `.json`, so `answers.json` is a list of `{"question_id", "answer"}` entries. Any other extension (e.g. `team.csv`) writes the kit's CSV (`question_id,answer`) instead.

If the question file comes separately from the building folders, add `--questions <file>` (`.jsonl`, or `.csv` like the kit's `test/questions.csv`: `question_id, building_id, kind, question`).

`--data` takes one building folder (with its `building.json`), a split folder with one subfolder per building (`data/dev`), or a folder of splits (`data`). Without `--questions`, every building's own `questions.jsonl` is answered, plus `questions_basic.jsonl` (warm-up questions) unless you pass `--no-basic`.

## What it does

1. **Ingest (graph only).** Every building goes through the ingestion pipeline in `graph` mode: raw validation and cleanup, relationship graph build, graph validation. The 3D stages and the summarizer don't run. This mode is deterministic: no cleanup agent and no orchestrator model. Cleanup only makes fixes that leave the measured geometry alone (rotation normalisation, neutralising instruction-like text, duplicate entrances). The remaining data quirks (overlapping outlines, doors slightly off a wall, floors centred per floor) are reported and kept. Each building must **pass**: the run completes without a review, every stage passes, and floors, space outlines, openings and connectors in the cleaned building are identical to the source.
2. **Answer.** Each question goes to the app's chat agent (the same LangGraph graph, building primer and navgraph tools as `/api/chat`) on the cleaned building, with **Claude Haiku 5.5** and a strict answer format: one `Answer:` line with one number, one room, or a route as the ordered list of space ids. Every question gets a fresh navigator and conversation, so a closure or new door from one question never leaks into the next. 16 questions run in parallel by default (`--workers`); a question that hits the rate limit is retried with backoff. Route answers then pass a deterministic normaliser that writes the answer line as exact space ids: it resolves room names (also lift and stair names shared across floors, by which spaces connect) and, if the line is not a route, takes the `spaces` of the agent's last `route` tool call.
3. **Score** (when the questions carry answers). Benchmark questions are scored with the official scorer. Warm-up routes are scored with the organizers' route rules (below), and locate and count questions by the room or number on the answer line.

Outputs go to `--out` (default `benchmark_results/graph-<date>-<time>`):

| File | Content |
|---|---|
| `submission.csv` | `question_id,answer` with the full reply per question (`--submission` gets the same, as JSON for a `.json` path) |
| `results.jsonl` | Question, expected answer, reply, score and note per question |
| `pipeline.json` | Per building: run status, stage results, problems found |
| `report.html` | Static results page (open it straight from disk, no server): summary cards, score per kind, pipeline results, every miss with question, expected answer and reply |
| `report.md` | The same summary as Markdown |
| `workspace/` | The pipeline's workspace: cleaned `building.json`, issues, patches, `graph/room_graph.json` |

The exit code is 0 only when every building passed the pipeline, no question failed to run and, with `--min-score 0.95`, the benchmark accuracy reached that share. So the same command doubles as the end-to-end check before the Sunday run.

## Route rules (kit 1.1.0, what the judges grade with)

- Walking distance is measured inside each space from door to door, 15 cm off the walls, around corners and never through a wall. Furniture and machines are not obstacles.
- Stairs cost 2 m per metre climbed. A lift costs 15 m plus 1 m per metre climbed. There is no shortcut round the outside of the building.
- A route to a machine or item ends in the room or bay that holds it.
- Rules: step-free means lifts, ramps and level doors only; forklift means aisles and openings at least 3 m wide; closed areas are avoided; production bays are destinations, never shortcuts. If no route exists under the rules, the answer says so.
- A route answer is the ordered list of spaces. It scores 0 if a step doesn't go through a real door, opening, stair or lift, or breaks a rule. Otherwise it scores best length ÷ its length, with full marks within 1 %.

The kit copy in `problem_statement/` is **v1.0.0**. Its errand and what-if-door answers still use the old route model (straight lines through room anchors, 12 m per floor, free walks round the outside). The benchmark recomputes those expected values from each question's `inputs` with the vendored official kit 1.1.0 code (`app/navigation/ported/operations.py`) and lists every changed value in `results.jsonl` (`refreshed`). On kit 1.1.0 data nothing changes. Warm-up routes are judged the same way: the best route under the 1.1.0 rules, not the kit's 1.0.0 `space_path`.

## Useful variants

Run these from `backend/`.

```bash
uv run poe benchmark                                     # whole example set: kit train + dev
uv run poe benchmark --data ../problem_statement/industrial-agents-hackathon-2026/data/dev --per-kind 3   # quick check
uv run poe benchmark --ingest-only                       # pipeline check only, no API calls
uv run poe benchmark --render ../benchmark_results/<run>  # rebuild report.html of an earlier run
uv run poe benchmark --rerun ../benchmark_results/<run>   # answer only what that run got wrong
uv run poe benchmark --model claude-sonnet-5-5           # another answer model
uv run poe ingest <dataset> --graph-only                 # the graph-only pipeline on its own
```

The API starts the same variant with `POST /api/pipeline-runs {"dataset": "...", "mode": "graph"}`.

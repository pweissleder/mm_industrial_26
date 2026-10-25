"""Graph-only benchmark: ingest each building with the pipeline's "graph" mode, then answer
its questions with a small model over the navgraph tools and score the replies.

- `data`: finds buildings and their questions (kit splits, one building, or a question file);
- `answerer`: the chat agent graph with a benchmark answer format and a cheaper model;
- `scoring`: the official scorer for benchmark questions, the organizers' route rules for
  warm-up routes, and expected answers refreshed to the kit 1.1.0 route model.

`scripts/benchmark.py` is the command line (`uv run poe benchmark`); see SUBMISSION/BENCHMARK.md.
"""

# Dialog simulation `live_haiku_recheck`

```
{
  "label": "live_haiku_recheck",
  "mode": "live",
  "model": "claude-haiku-5-5",
  "effort": "medium",
  "scenarios_passed": 1,
  "scenarios": 2,
  "turns_passed": 2,
  "turns": 3,
  "estimated_usd": 0.0021,
  "latency_ms": {
    "claude_median": 8252.9,
    "claude_max": 10248.5,
    "tools_median": 24.6
  },
  "concise_1_2_sentences": 1
}
```

| Scenario | Result | Failed checks |
|---|---|---|
| toilet_unknown_start | FAIL | route_to_nearest, tools |
| break_relax | PASS |  |

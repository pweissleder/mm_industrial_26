# Dialog simulation `live_v1`

```
{
  "label": "live_v1",
  "mode": "live",
  "model": "claude-sonnet-5-5",
  "effort": "medium",
  "scenarios_passed": 7,
  "scenarios": 13,
  "turns_passed": 15,
  "turns": 21,
  "estimated_usd": 0.2663,
  "latency_ms": {
    "claude_median": 7863.9,
    "claude_max": 18984.8,
    "tools_median": 16.1
  },
  "concise_1_2_sentences": 6
}
```

| Scenario | Result | Failed checks |
|---|---|---|
| coffee_nearest | PASS |  |
| toilet_unknown_start | PASS |  |
| break_relax | FAIL | short |
| cnc_ambiguous | PASS |  |
| stt_errors | FAIL | short |
| unknown_room | FAIL | short |
| facility_unavailable | PASS |  |
| closure_flow | FAIL | assumed_closed, short, tools |
| wheelchair_floors | FAIL | short |
| trolley_precision | FAIL | short |
| long_campus | PASS |  |
| benchmark_fact | PASS |  |
| missing_destination | PASS |  |

# Dialog simulation `live_v2`

```
{
  "label": "live_v2",
  "mode": "live",
  "model": "claude-sonnet-5-5",
  "effort": "medium",
  "scenarios_passed": 11,
  "scenarios": 13,
  "turns_passed": 19,
  "turns": 21,
  "estimated_usd": 0.2554,
  "latency_ms": {
    "claude_median": 7580.0,
    "claude_max": 15947.0,
    "tools_median": 16.9
  },
  "concise_1_2_sentences": 14
}
```

| Scenario | Result | Failed checks |
|---|---|---|
| coffee_nearest | PASS |  |
| toilet_unknown_start | PASS |  |
| break_relax | PASS |  |
| cnc_ambiguous | FAIL | asks |
| stt_errors | PASS |  |
| unknown_room | PASS |  |
| facility_unavailable | PASS |  |
| closure_flow | PASS |  |
| wheelchair_floors | PASS |  |
| trolley_precision | FAIL | short |
| long_campus | PASS |  |
| benchmark_fact | PASS |  |
| missing_destination | PASS |  |

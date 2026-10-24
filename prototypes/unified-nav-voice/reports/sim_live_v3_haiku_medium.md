# Dialog simulation `live_v3_haiku_medium`

```
{
  "label": "live_v3_haiku_medium",
  "mode": "live",
  "model": "claude-haiku-5-5",
  "effort": "medium",
  "scenarios_passed": 11,
  "scenarios": 13,
  "turns_passed": 19,
  "turns": 21,
  "estimated_usd": 0.3082,
  "latency_ms": {
    "claude_median": 5864.2,
    "claude_max": 15309.0,
    "tools_median": 15.6
  },
  "concise_1_2_sentences": 21
}
```

| Scenario | Result | Failed checks |
|---|---|---|
| coffee_nearest | PASS |  |
| toilet_unknown_start | FAIL | route_to_nearest, tools |
| break_relax | FAIL | mentions_any |
| cnc_ambiguous | PASS |  |
| stt_errors | PASS |  |
| unknown_room | PASS |  |
| facility_unavailable | PASS |  |
| closure_flow | PASS |  |
| wheelchair_floors | PASS |  |
| trolley_precision | PASS |  |
| long_campus | PASS |  |
| benchmark_fact | PASS |  |
| missing_destination | PASS |  |

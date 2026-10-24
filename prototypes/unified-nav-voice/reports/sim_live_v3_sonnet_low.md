# Dialog simulation `live_v3_sonnet_low`

```
{
  "label": "live_v3_sonnet_low",
  "mode": "live",
  "model": "claude-sonnet-5-5",
  "effort": "low",
  "scenarios_passed": 11,
  "scenarios": 13,
  "turns_passed": 19,
  "turns": 21,
  "estimated_usd": 0.2307,
  "latency_ms": {
    "claude_median": 5908.5,
    "claude_max": 11653.0,
    "tools_median": 16.0
  },
  "concise_1_2_sentences": 16
}
```

| Scenario | Result | Failed checks |
|---|---|---|
| coffee_nearest | PASS |  |
| toilet_unknown_start | FAIL | mentions_any |
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

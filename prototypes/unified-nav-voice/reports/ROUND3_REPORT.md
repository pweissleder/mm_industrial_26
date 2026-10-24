# Round 3 — benchmark parity, customer dialogs, robustness (2026-10-24)

Reproduce: `python -B benchmark_eval.py` (offline, ~85 s) · `python -B sim/run_sim.py --offline` ·
`pwsh scripts/Run-Sim.ps1 --live --label x` (paid) · `python -B robustness_audit.py --label x` ·
`python -B -m unittest discover` (132 tests).

## 1. Official benchmark (kit 1.1.0 = starter `main` dc03c44, official scorer, Train+Dev)

| | Before (175542d, kit 1.0.0 model) | After |
|---|---|---|
| Benchmark questions | 568/655 (dev 120/138, train 448/517) | **655/655** |
| errand / what_if_door | 10/93 / 0/4 | 93/93 / 4/4 |
| other 7 kinds | 100 % | 100 % |
| Warm-up routes incl. tours (1,338) — reachability | 1337/1338 | **1338/1338** |
| space_path exact (1,105 reachable) | 1102/1105 | **1105/1105** |
| element_path exact | 1102/1105 | **1105/1105** |
| length ≤ 1 % / ≤ 0.01 m | 335 / 11 of 1105 | **1105 / 1105** |
| locate / count (deterministic lookup) | 553/553 / 239/239 | 553/553 / 239/239 |

All sources at 100 % (osm_indoor, procedural, resplan, swiss_dwellings). Benchmark operations get only
the official `inputs`; warm-up routes take endpoints from the answer IDs (router test, like the kit's
`--oracle`; natural-language parsing is tested separately by the dialog matrix). Tour totals may differ
by 0.01 m because the dataset sums rounded legs. Earlier ad-hoc counts were wrong: closures were not
applied (corrected: 160/161 paths, 73/161 lengths ≤1 %, 28/28 unreachable) and 121/138 was 120/138.
Details: `benchmark_1_1_0.md` (after), `benchmark_1_1_0_before.md`.

Official router = kit 1.1.0 `BuildingMap` graph/search (cross-checked on every call); drawn segments
replay the official walk. Production-zone and forklift rules are **not in kit 1.1.0** → opt-in flags,
labelled unofficial. Precision routing stays a separate contract.

## 2. Customer-dialog matrix (13 scenarios, 21 turns, deterministic checks)

Scenarios: nearest coffee + "how do I get there?", nearest toilet without position, vague "break"
intent (campus), ambiguous CNC mill, speech-recognition errors, unknown room/floor, missing facility,
what-if → close (Confirm) → route → reopen (Confirm), wheelchair multi-floor + follow-up, 0.9 m trolley
avoiding machines, long campus route, benchmark fact, missing destination.

| Run | Scenarios | Turns | 1–2 sentences | Claude median / max | Cost |
|---|---|---|---|---|---|
| offline (scripted plan, real tools) | 13/13 | 21/21 | – | – | 0 |
| live v1 Sonnet 5.5 medium (before fixes) | 7/13 | 15/21 | 6/21 | 7.9 s / 19.0 s | $0.27 |
| live v2 after prompt/tool fixes | 11/13 | 19/21 | 14/21 | 7.6 s / 15.9 s | $0.26 |
| **live v3 final, Sonnet 5.5 medium** | **13/13** | **21/21** | 15/21 | **6.5 s / 10.9 s** | $0.26 |
| live Sonnet 5.5 low | 11/13 | 19/21 | 16/21 | 5.9 s / 11.7 s | $0.23 |
| live Haiku 5.5 medium | 11/13 | 19/21 | 21/21 | 5.9 s / 15.3 s | ≈$0.02 |

Tool execution median 17 ms; map rendering is shown per reply in the UI ("map … ms").
Decision: keep Sonnet 5.5 medium (low is barely faster and failed two scenarios; Haiku is ~15× cheaper
but skipped routing once). Total live spend this round ≈ $1.05 (estimate from token usage).

Fixed from the live failures: replies too long (hard 2-sentence rule + per-turn reminder, no narration);
"what changes if X is closed" without destination (new `preview_closure` tool, nothing changes);
nearest place ranking by official walking distance (`near_start`), did-you-mean for unknown names,
deterministic step-by-step directions shown under each reply (only real doors/lifts/stairs), default
mode = official route, 8 instead of 6 tool rounds, per-model cost estimate.

## 3. Precision routing

- Profile `none` (walls/doors only, endpoint assets still obstacles), user width/safety unchanged.
- Controlled snapping (`snap_m` ≤ 2, UI 1 m): a blocked point moves to the nearest admissible point
  of the same room only if the line never crosses a wall or another obstacle; reported as warning.
- Wide open apertures (≥ 2.5 m) blocked at the centre are retried off-centre inside the opening
  interval with full clearance checks (fixed: 0.9 m trolley logistics → warehouse was "unsupported").
- Independent audit (`robustness_audit.py`, seed 24102026, 404 cases): factory success 221 → 222,
  campus 24 → 24, **0 unsafe**; the audit's door check now accepts off-centre crossings inside the
  usable interval (walls, obstacles, clearance still verified independently).
- UI: official route = dashed purple ("not obstacle-checked"), checked precision route = blue.

## 4. Open limits

- Precision routing only for procedural buildings; floor changes are schematic; centre-first crossing
  is not globally optimal (a wider trolley can get a slightly shorter path).
- osm-way360544293: first official graph ~25 s (kit's own walk computation), later ~35 ms.
- Real microphone test in the browser still to be done by a person.
- Not yet merged into the team's backend/frontend.

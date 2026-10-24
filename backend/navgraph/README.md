# navgraph: navigation and intelligence layer for buildings

One Python package between the raw `building.json` files and the agent. It turns a plan into a
two-level graph, answers routing and geometry questions with exact numbers, and exposes everything
as a small set of JSON tools an LLM agent can call.

```
building.json ──► Building (model.py)       typed facts + edits (close lift, add door, move machine)
                    │
                    ├─► RoomGraph   (L1)     space = node, door/stair/lift hop = edge
                    ├─► RoomInterior (L2)    inside one space: doors, stops, anchor, things + distances
                    └─► NavGraph             L2 interiors glued at shared doors → shortest paths
                          │  Profile  (rules.py)  who moves: walk, step_free, forklift, wide_load, closures
                          │  CostModel(costs.py)  how long: OrganizerCost / DatasetCost
                          ▼
                 scoring.py    the organizers' checker: validate() + measure() a list of spaces,
                               robust_route() = experimental worst-case comparison (not the default)
                 analysis.py   areas, largest room, stacking, shared walls, 3D distance, errands, what-if
                 builder.py    create/extend a building by conversation + validate()
                 agent.py      BuildingIntelligence: primer() + tool_schemas() + call(name, args)
```

## Quick start

```python
from navgraph.agent import BuildingIntelligence

bi = BuildingIntelligence.load("data/dev/proc-factory-dev-003")      # graph builds lazily
system_prompt = bi.primer()            # 1-4k chars of building facts and rules for the agent
tools = bi.tool_schemas()              # Anthropic tool definitions (20 tools)
bi.call("route", {"start": "Office 0.23", "goal": "Kitchen 1.08", "profile": "step_free"})
# -> {"reachable": true, "length_m": 59.39, "spaces": [...ids...], "space_names": [...],
#     "elements": [door, door, lift, ...], "directions": ["Start in Office 0.23 ...", ...]}
```

Wire it into any tool loop: pass `tools` to the Messages API and answer each `tool_use` block with
`bi.call_json(block.name, block.input)`.

Lower-level use (no agent):

```python
from navgraph import Building, NavGraph, OrganizerCost, rules
b = Building.load("data/dev/proc-factory-dev-003")
nav = NavGraph(b, OrganizerCost())
r = nav.route("proc-factory-dev-003-F0-S030", "proc-factory-dev-003-F0-S046", rules.STEP_FREE)
r.spaces, r.length_m          # ordered spaces for the scorer, metres
```

## Why two levels

Crossing a room costs a different amount depending on which door you enter and leave by, so a
room-node graph cannot find the shortest route. The agent reasons on L1 (rooms and their
connections). Routes are searched on the glued L2 graph and reported back as L1 space lists.

## The tools

| Group | Tools |
|---|---|
| Understand | `building_overview`, `find`, `describe_space`, `list_rooms`, `spatial`, `room_interior` |
| Move | `route` (with `via`, `profile`, `min_width`, `avoid`), `check_route`, `reachable`, `nearest`, `distance_to_exit` |
| Change | `set_closed`, `add_door`, `what_if_door`, `move_asset`, `save` |
| Build | `add_floor`, `add_space`, `add_entrance`, `add_connector`, `validate` |

Every tool accepts what a person types ("Office 0.30 (Ground floor)", "0.30", "CNC mill H-A1-A",
"the stove") as well as ids. It never guesses: an unclear name returns
`{"error": "ambiguous", "candidates": [...]}`, and the agent should ask the user.

## Two metrics, one graph

| | `OrganizerCost` (default) | `DatasetCost` |
|---|---|---|
| Inside a space | door to door, 15 cm off walls, around corners | straight lines via the space's anchor point |
| Stairs | 2 m per metre climbed | 12 m per floor |
| Lift | 15 m per ride + 1 m per metre | 15 m + 6 m per floor hop |
| Outside | straight line between entrances (configurable) | free |
| Use for | route answers scored by the organizers | numbers in the provided answer files (errand, what-if) |

`BuildingIntelligence.load(path, metric="dataset")` switches the facade to the dataset metric.

## Verified

`python tests/check_routes.py data/dev` and `python tests/check_benchmark.py data/dev data/train`:

- warm-up routes: **1338/1338** lengths match to the centimetre (dev + train), including step-free,
  closures, tours and unreachable cases
- benchmark questions answered from their inputs: **655/655** (all 9 kinds, dev + train)

Counting rules found in the data:
- "rooms" in reachability/delivery counts are `type == "room"`, start room included
- shared walls count rooms and halls, not corridors, stairwells, lifts or balconies

## How route answers are scored, and what the code does about it

The organizers check the **ordered list of spaces** (each step must be a real door, opening, stair or
lift allowed by the rules), then measure it with their own distance rule. Score = best ÷ ours; within
1 % is full marks; invalid = 0. So:

- `scoring.validate()` repeats their validity check. `route` never returns a route that fails it.
- `scoring.measure()` repeats their measurement for any list of spaces: the shortest walk through
  exactly those spaces, choosing the best door where two spaces share several. On all 344 dev
  routes it equals the router's own length to the centimetre, under both metrics.
- The `route` tool uses the strict router with one cost model, then re-validates the result
  against the original request. Rules are never loosened: no route under the rules is the answer.
- `scoring.robust_route()` is an experimental comparison: the list of spaces with the best
  worst-case score over our own guesses at the open details (mean 0.998 on dev). It is not an
  official score and is not used for answers.
- Closures: `Building.closed` and per-query `avoid` meet in `Profile.under(building)`, the one
  availability check used by every search and by `validate`. Closures are saved in `"closed"`.
- Geometry fails safe: disconnected pieces or a failed visibility search mean unreachable, never a
  straight line. Where the 15 cm clearance splits a space at a drawing sliver (39 of 18k spaces in
  dev + train), the walk uses the room's own outline instead.
- Routes stay **indoors** unless there is no indoor route, because "outside" is not one of the
  spaces in the organizers' note and an invalid route scores 0. If outside does count, staying
  indoors costs full marks on 4 of 112 dev routes (lowest 0.66).
- `check_route` lets the agent (or you) check any route by hand.

Rule mapping:

| Organizer rule | Where |
|---|---|
| Door to door, 15 cm off walls, around corners | `geometry.WalkableArea`, `OrganizerCost` |
| Stairs 2 m per m climbed; lift 15 m + 1 m per m | `OrganizerCost` |
| Furniture is no obstacle; a route to a thing ends in its room | outlines only; `resolve_space` maps things to rooms |
| Without stairs: lifts, ramps and doors only | `rules.STEP_FREE` (crossing a stairwell landing is allowed, as in the dev answers) |
| Forklift: aisles and openings >= 3 m | `rules.FORKLIFT` |
| Closed areas avoided | `Building.set_closed`, `avoid=[...]` |
| Production bays: destination only | `Space.is_production_bay` (hall + `*_zone`); `no_pass_through` |
| No route → say so | `reachable: false` + `explain` |

## Open points to confirm with the organizers

1. Where does a route start and end inside a room: at a point inside the room or at its door?
   Answers use `OrganizerCost(endpoint="anchor")`; `robust_route` compares both.
2. Is the lift's 15 m charged once per ride or per floor? Both are covered.
3. Is "outside" a valid step (out one entrance, back in another)? Currently avoided unless needed.
4. Which factory zones count as "production bays"? Currently every hall `*_zone`. That is the safe
   side: a route is never invalid because of it, at worst slightly longer.
5. Which spaces are "forklift aisles"? Currently halls (not lobbies) and spaces named "aisle".

Requirements: Python 3.10+, `shapely>=2`, `networkx>=3`.

"""Customer-dialog matrix for the AriNav assistant (ported from prototypes/unified-nav-voice/sim).

Each scenario runs in one fresh assistant session on one building. Turn fields:
  user    - what the customer says (may contain deliberate speech-recognition errors)
  expect  - deterministic checks on the actual turn result (see dialog_sim.check)
  confirm - after the turn the harness presses Confirm (True) / Cancel (False) on the newest
            proposal, like a person in the UI would

Differences to the prototype matrix (the tool set changed, the dialogs did not):
  - coffee_nearest turn 1: `tools: [find_places]` -> `tools_any: [find_places, find_nearest]`
    (the prototype found the nearest place with find_places + near_start; the new backend has a
    dedicated find_nearest tool and no near_start field).
  - benchmark_fact: `benchmark_operation` -> `building_fact` (renamed tool).
"""

from typing import Any

F = "proc-factory-dev-003"
C = "proc-campus-dev-009"


def f(suffix: str) -> str:
    return f"{F}-{suffix}"


def c(suffix: str) -> str:
    return f"{C}-{suffix}"


SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "coffee_nearest",
        "building": F,
        "title": 'Coffee near me, then "how do I get there?"',
        "turns": [
            {
                "user": "I'm in Office 0.38. Where can I get a coffee?",
                "expect": {
                    "state_start": f("F0-S038"),
                    "tools_any": ["find_places", "find_nearest"],
                    "mentions_nearest": "coffee",
                    "short": True,
                },
            },
            {
                "user": "And how do I get there?",
                "expect": {
                    "tools": ["plan_route"],
                    "route_from": f("F0-S038"),
                    "route_to_nearest": "coffee",
                    "short": True,
                },
            },
        ],
    },
    {
        "id": "toilet_unknown_start",
        "building": F,
        "title": "Nearest toilet without a known position",
        "turns": [
            {
                "user": "Where is the nearest toilet?",
                "expect": {"no_route": True, "mentions_any": ["toilet"], "asks": True},
            },
            {
                "user": "I'm in the quality lab.",
                "expect": {
                    "state_start": f("F0-S021"),
                    "tools": ["plan_route"],
                    "route_to_nearest": "toilet",
                    "short": True,
                },
            },
        ],
    },
    {
        "id": "break_relax",
        "building": C,
        "title": "Vague intent: somewhere to relax",
        "turns": [
            {
                "user": "I'm in Lab 0.02 and need a break. Where can I relax and get something "
                "to eat?",
                "expect": {
                    "state_start": c("F0-S002"),
                    "mentions_any": ["canteen", "kitchen"],
                    "short": True,
                },
            },
        ],
    },
    {
        "id": "cnc_ambiguous",
        "building": F,
        "title": "Ambiguous machine name",
        "turns": [
            {
                "user": "Where is the CNC mill?",
                "expect": {"asks": True, "mentions_any": ["three", "3"], "no_route": True},
            },
            {
                "user": "The B one. I am at hall access 0.19.",
                "expect": {"route_from": f("F0-S019"), "route_to": f("A00002"), "short": True},
            },
        ],
    },
    {
        "id": "stt_errors",
        "building": F,
        "title": "Speech-recognition errors in room names",
        "turns": [
            {
                "user": "How do I get from the hall axis zero point one nine to the quality lap?",
                "expect": {"route_from": f("F0-S019"), "route_to": f("F0-S021"), "short": True},
            },
        ],
    },
    {
        "id": "unknown_room",
        "building": F,
        "title": "Unknown room and floor",
        "turns": [
            {
                "user": "Take me to the cafeteria on floor 7.",
                "expect": {"no_route": True, "honest_missing": True, "short": True},
            },
        ],
    },
    {
        "id": "facility_unavailable",
        "building": F,
        "title": "Facility that does not exist",
        "turns": [
            {
                "user": "Is there a gym or a shower here?",
                "expect": {"no_route": True, "honest_missing": True, "short": True},
            },
        ],
    },
    {
        "id": "closure_flow",
        "building": F,
        "title": "What-if, closure with confirmation, then undo",
        "turns": [
            {
                "user": "I'm in Meeting room 0.20. What would happen if the office corridor 0.16 "
                "were closed?",
                "expect": {
                    "tools_any": ["preview_closure", "plan_route"],
                    "previewed_closed": f("F0-S016"),
                    "unchanged": True,
                    "short": True,
                },
            },
            {
                "user": "Okay, close the corridor for cleaning.",
                "expect": {"proposal": ["close", f("F0-S016")], "unchanged": True, "short": True},
                "confirm": True,
            },
            {
                "user": "How do I get to the quality lab now?",
                "expect": {"closed_now": f("F0-S016"), "route_unreachable": True, "short": True},
            },
            {
                "user": "Cleaning is done, please open the corridor again.",
                "expect": {"proposal": ["open", f("F0-S016")], "short": True},
                "confirm": True,
            },
            {
                "user": "And now?",
                "expect": {
                    "route_found": True,
                    "route_to": f("F0-S021"),
                    "short": True,
                    "open_now": f("F0-S016"),
                },
            },
        ],
    },
    {
        "id": "wheelchair_floors",
        "building": F,
        "title": "Wheelchair user, upper floor",
        "turns": [
            {
                "user": "I use a wheelchair. How do I get from hall access 0.19 to Office 1.04?",
                "expect": {
                    "state_step_free": True,
                    "route_to": f("F1-S004"),
                    "uses": "lift",
                    "mentions_any": ["lift", "elevator"],
                    "short": True,
                },
            },
            {
                "user": "And then to the kitchen 1.07?",
                "expect": {
                    "route_from": f("F1-S004"),
                    "route_to": f("F1-S007"),
                    "state_step_free": True,
                    "short": True,
                },
            },
        ],
    },
    {
        "id": "trolley_precision",
        "building": F,
        "title": "Wide trolley avoiding machines",
        "turns": [
            {
                "user": "I'm pushing a 0.9 metre wide trolley from logistics zone H-C5 to "
                "warehouse zone H-A5. I need to stay clear of the machines.",
                "expect": {
                    "state_mode": "precision",
                    "state_width": 0.9,
                    "tools": ["plan_route"],
                    "short": True,
                },
            },
        ],
    },
    {
        "id": "long_campus",
        "building": C,
        "title": "Long multi-floor route in a campus building",
        "turns": [
            {
                "user": "From the lecture hall 0.31 to Lab 2.08, please.",
                "expect": {
                    "route_from": c("F0-S031"),
                    "route_to": c("F2-S008"),
                    "route_found": True,
                    "short": True,
                },
            },
        ],
    },
    {
        "id": "benchmark_fact",
        "building": F,
        "title": "Building fact question",
        "turns": [
            {
                "user": "Which is the largest kitchen on floor 1, and how big is it?",
                "expect": {"tools": ["building_fact"], "mentions_any": ["1.14"], "short": True},
            },
        ],
    },
    {
        "id": "missing_destination",
        "building": F,
        "title": "Route request without destination",
        "turns": [
            {
                "user": "I am in Office 0.23. Can you show me the way?",
                "expect": {
                    "state_start": f("F0-S023"),
                    "no_route": True,
                    "asks": True,
                    "short": True,
                },
            },
        ],
    },
]

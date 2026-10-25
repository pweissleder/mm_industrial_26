"""Strict tool schemas the model may call (at most 16 union-typed parameters: API limit)."""

from typing import Any

ENDPOINT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "id"],
    "properties": {
        "kind": {"type": "string", "enum": ["space", "asset", "opening"]},
        "id": {"type": "string", "description": "Exact ID from a find_places/find_nearest result"},
    },
}
NULLABLE_ENDPOINT: dict[str, Any] = {"anyOf": [ENDPOINT, {"type": "null"}]}
NULLABLE_BOOL: dict[str, Any] = {"type": ["boolean", "null"]}
NULLABLE_NUMBER: dict[str, Any] = {"type": ["number", "null"]}
NULLABLE_MODE: dict[str, Any] = {
    "anyOf": [{"type": "string", "enum": ["official", "precision"]}, {"type": "null"}]
}
NULLABLE_PROFILE: dict[str, Any] = {
    "anyOf": [{"type": "string", "enum": ["stationary", "all", "none"]}, {"type": "null"}]
}
OPERATIONS = [
    "largest_room",
    "floor_area_total",
    "errand",
    "wide_delivery_count",
    "step_free_reachable_count",
    "room_stacked",
    "shared_walls_count",
    "straight_line_3d",
    "what_if_door",
]


def tool(name: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": list(properties),
            "properties": properties,
        },
    }


def build_tools(operation_fields: str) -> list[dict[str, Any]]:
    return [
        tool(
            "find_places",
            "Search the active building's rooms, equipment, doors/exits and lifts/stairs by name, "
            "type "
            "or number. Read-only: it never changes the user's start or destination.",
            {
                "query": {
                    "type": "string",
                    "description": 'e.g. "hand dryer", "office 1.12", "exit"',
                },
                "kind": {
                    "anyOf": [
                        {
                            "type": "string",
                            "enum": ["room", "equipment", "door", "exit", "lift_or_stairs"],
                        },
                        {"type": "null"},
                    ]
                },
                "floor_level": {"type": ["integer", "null"], "description": "0 = ground floor"},
            },
        ),
        tool(
            "find_nearest",
            "Closest reachable places from the remembered start, ranked by official walking "
            "distance "
            "(not straight-line). category: 'exit' (exit doors), 'room_type:<type>' (e.g. toilet, "
            "kitchen, canteen, library), 'asset_type:<type>' (e.g. coffee_machine, hand_dryer) or "
            "'query:<words>'. With and_route=true it also computes and shows the route to the "
            "closest "
            "one in the same step (preferred for 'take me to the nearest ...').",
            {
                "category": {"type": "string"},
                "step_free": NULLABLE_BOOL,
                "and_route": {"type": "boolean"},
            },
        ),
        tool(
            "update_session",
            "Remember what the USER stated: their position (start), destination, routing mode or "
            "needs. "
            "null keeps a field. Never call it just because a search found something.",
            {
                "start": NULLABLE_ENDPOINT,
                "destination": NULLABLE_ENDPOINT,
                "clear_start": {"type": "boolean"},
                "clear_destination": {"type": "boolean"},
                "mode": NULLABLE_MODE,
                "step_free": NULLABLE_BOOL,
                "profile": NULLABLE_PROFILE,
                "width_m": NULLABLE_NUMBER,
                "safety_m": NULLABLE_NUMBER,
            },
        ),
        tool(
            "plan_route",
            "Compute and show a route with the deterministic router. null start/destination use "
            "the "
            "session. Destinations may be rooms, equipment or doors/exits (the route then ends at "
            "that "
            "door). assume_closed = IDs treated as closed for this preview only.",
            {
                "start": NULLABLE_ENDPOINT,
                "destination": NULLABLE_ENDPOINT,
                "mode": NULLABLE_MODE,
                "step_free": NULLABLE_BOOL,
                "assume_closed": {"type": "array", "items": {"type": "string"}},
            },
        ),
        tool(
            "preview_closure",
            "What changes if rooms, doors, lifts or stairs were closed (nothing is changed): "
            "rooms that "
            "become unreachable or much longer to reach from the start (or from the entrances).",
            {"element_ids": {"type": "array", "items": {"type": "string"}}},
        ),
        tool(
            "propose_change",
            "Propose closing or reopening a room, door, lift or staircase. Nothing changes until "
            "the "
            "user presses Confirm.",
            {
                "action": {"type": "string", "enum": ["close", "open"]},
                "element_id": {"type": "string"},
            },
        ),
        tool(
            "building_fact",
            "Deterministic building facts for explicit fact questions only (largest room of a "
            "type, "
            "total area, room above/below, shared walls, reachable-room counts, straight-line "
            "distance, errands, what-if doors). Not for finding or routing to a place. "
            "arguments_json "
            "is a JSON object string with exactly these fields (? = optional): " + operation_fields,
            {
                "operation": {"type": "string", "enum": OPERATIONS},
                "arguments_json": {"type": "string"},
            },
        ),
        tool(
            "list_buildings",
            "List available buildings, optionally filtered by words.",
            {"query": {"type": ["string", "null"]}},
        ),
        tool(
            "select_building",
            "Switch the active building (clears start, destination and conversation context).",
            {"building_id": {"type": "string"}},
        ),
        tool(
            "recall_memory",
            "Memory across all conversations (read-only). topic 'stats': how often each place "
            "type was searched (e.g. toilet, coffee_machine), routes planned, metres, floor "
            "changes and metres climbed/descended by stairs vs lift; 'recent_chats': the latest "
            "messages; 'search_chats': messages containing query; 'recent_routes': the last "
            "planned routes.",
            {
                "topic": {
                    "type": "string",
                    "enum": ["stats", "recent_chats", "search_chats", "recent_routes"],
                },
                "query": {"type": "string", "description": "Words for search_chats, else ''"},
            },
        ),
    ]


def union_count(tools: list[dict[str, Any]]) -> int:
    return sum(
        1
        for t in tools
        for v in t["input_schema"]["properties"].values()
        if "anyOf" in v or isinstance(v.get("type"), list)
    )

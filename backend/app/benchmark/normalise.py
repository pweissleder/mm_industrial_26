"""Puts a route answer into the exact form the graders read: `Answer: <id> -> <id> -> ...`.

A small model knows the route but is sloppy about writing it down: room names instead of ids
(lifts and stairwells share one name on every floor), a capitalised first id, a stray phrase
in front, or only the destination. This step is deterministic and uses only the building and
the agent's own tool results:

1. Each token of the answer line becomes a space id: an id in any letter case, an id inside
   the token, or a name / room number. Repeated names are resolved by which spaces actually
   connect (door, opening, stair or lift), so "Lift 1 -> Lift 1" becomes two lift stops.
2. If the line still isn't a route and the question asks for one, the `spaces` of the
   agent's last `route` tool result are used (the kit's own evaluator also scores the route
   the agent computed, not its prose).
3. A `Route JSON:` line in the kit's answer structure (`from_space_id`, `to_space_id`,
   `reachable`, `space_path`, `element_path`, `length_m`) goes right above the Answer line,
   which stays the last line the scorer reads.

Answers that say there is no route are left alone, and so is everything that isn't a route.
"""

import json
import math
import re
from typing import Any

from app.benchmark.scoring import NO_ROUTE, ROUTE_KINDS, SEPARATORS, STRIP
from app.domain.building import BuildingFile
from app.navigation.official.score import ANSWER_LINE
from navgraph import scoring as route_rules
from navgraph.agent import BuildingIntelligence, ToolError
from navgraph.model import OUTSIDE, Building
from navgraph.rules import WALK

ARROW = re.compile(r"->|→|=>")
WORD = re.compile(r"[\w.-]+")
ROUTE_JSON_LINE = re.compile(r"^Route JSON:.*\n?", flags=re.MULTILINE)


def route_results(messages: list[Any]) -> list[dict[str, Any]]:
    """Parsed results of the agent's `route` tool calls, in call order."""
    names = {
        call["id"]: call["name"]
        for message in messages
        for call in getattr(message, "tool_calls", None) or []
    }
    results = []
    for message in messages:
        call_id = getattr(message, "tool_call_id", None)
        if call_id is None or names.get(call_id) != "route":
            continue
        try:
            result = json.loads(message.content)
        except (TypeError, ValueError):
            continue
        if isinstance(result, dict) and "reachable" in result:
            results.append(result)
    return results


class AnswerNormaliser:
    def __init__(self, building: BuildingFile) -> None:
        self.bi = BuildingIntelligence(Building.from_dict(building.model_dump()))
        self.by_lower = {sid.lower(): sid for sid in self.bi.b.spaces}
        self.by_lower[OUTSIDE] = OUTSIDE

    def candidates(self, token: str) -> list[str]:
        low = token.lower()
        if low in self.by_lower:
            return [self.by_lower[low]]
        inside = [self.by_lower[w] for w in WORD.findall(low) if w in self.by_lower]
        if inside:
            return inside[-1:]  # "Office 0.20 route: <id>": the id ends the stray phrase
        try:
            return [self.bi.resolve_space(token)]
        except ToolError as error:
            found = error.payload.get("candidates", [])
            return [
                c["id"] for c in found if isinstance(c, dict) and c.get("id") in self.bi.b.spaces
            ]

    def connected(self, a: str, c: str) -> bool:
        return bool(route_rules.links(self.bi.b, a, c, WALK))

    def parse(self, text: str) -> list[str] | None:
        """The space ids the line names, or None when it can't be read as a connected route."""
        tokens = [t.strip(STRIP) for t in SEPARATORS.split(text)]
        layers = [self.candidates(t) for t in tokens if t]
        if len(layers) < 2 or not all(layers):
            return None
        # Reachability over the candidates of each token; one valid predecessor is enough.
        back: list[dict[str, str | None]] = [{c: None for c in layers[0]}]
        for layer in layers[1:]:
            step: dict[str, str | None] = {}
            for c in layer:
                for p in back[-1]:
                    if p == c or self.connected(p, c):
                        step[c] = p
                        break
            if not step:
                return None
            back.append(step)
        path = [next(iter(back[-1]))]
        for i in range(len(back) - 1, 0, -1):
            previous = back[i][path[-1]]
            assert previous is not None
            path.append(previous)
        path.reverse()
        return [s for i, s in enumerate(path) if i == 0 or s != path[i - 1]]

    def structure(self, ids: list[str] | None, routes: list[dict[str, Any]]) -> dict[str, Any]:
        """The route in the kit's answer structure; elements and length from the agent's
        matching `route` result, else from the building (first usable link, walking rule)."""
        if ids is None:
            return {"reachable": False}
        match = next((r for r in reversed(routes) if r.get("spaces") == ids), None)
        if match is not None:
            elements, length = list(match.get("elements") or []), match.get("length_m")
        else:
            steps = [
                route_rules.links(self.bi.b, a, c, WALK) for a, c in zip(ids, ids[1:], strict=False)
            ]
            elements = [step[0][1] for step in steps if step]
            measured = route_rules.measure(self.bi.nav, ids, WALK)
            length = round(measured, 2) if math.isfinite(measured) else None
        return {
            "from_space_id": ids[0],
            "to_space_id": ids[-1],
            "reachable": True,
            "space_path": ids,
            "element_path": elements,
            "length_m": length,
        }

    def normalise(self, reply: str, kind: str, routes: list[dict[str, Any]]) -> str:
        reply = ROUTE_JSON_LINE.sub("", reply)  # one structure line, written here
        matches = list(ANSWER_LINE.finditer(reply))
        text = matches[-1].group(1).strip() if matches else ""
        route_question = kind in ROUTE_KINDS or "route" in kind or "tour" in kind
        if not route_question and not ARROW.search(text):
            return reply  # a number or a room: not ours to touch
        ids: list[str] | None
        if text and NO_ROUTE.search(text):
            ids, line = None, f"Answer: {text}"
        elif (parsed := self.parse(text) if text else None) is not None:
            ids, line = parsed, "Answer: " + " -> ".join(parsed)
        elif route_question and routes:
            last = routes[-1]
            spaces = list(last.get("spaces") or [])
            ids = spaces if last["reachable"] and spaces else None
            line = "Answer: " + (" -> ".join(ids) if ids else "no route")
        else:
            return reply
        block = f"Route JSON: {json.dumps(self.structure(ids, routes), ensure_ascii=False)}\n{line}"
        if not matches:
            return f"{reply.rstrip()}\n{block}"
        start, end = matches[-1].span()
        return reply[:start] + block + reply[end:]

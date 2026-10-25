"""OFFLINE integration: the real app (create_app) with the real NavigationService on the in-repo
factory building; only Claude is replaced by the scripted stand-in. Covers the two user-reported
failures end to end (exit route ends at the exit door; an ambiguous follow-up ends with a normal
question) plus the one-scenario rule shared by AriNav, the assistant and the navgraph agent."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.assistant.service import MAX_ROUNDS
from app.config import BACKEND_DIR, Settings
from app.main import create_app
from tests.assistant.test_assistant_unit import Scripted, update

DATASET = "proc-factory-dev-003"
B = DATASET
OFFICE_F1 = f"{B}-F1-S004"  # Office 1.04, first floor
LIFT = f"{B}-V02"  # Office lift
SID = "integration-0001"


@pytest.fixture
def datasets_dir(tmp_path: Path) -> Path:
    shutil.copytree(BACKEND_DIR / "data" / "datasets" / DATASET, tmp_path / DATASET)
    return tmp_path


def make_client(settings: Settings, datasets_dir: Path, script: list[Any]) -> TestClient:
    settings.datasets_dir = datasets_dir
    return TestClient(create_app(settings, assistant_transport=Scripted(script)))


@pytest.fixture
def ingest() -> Any:
    def run(client: TestClient) -> None:
        assert client.post("/api/ingestions", json={"dataset": DATASET}).status_code == 201

    return run


def turn(client: TestClient, text: str, **ui: Any) -> dict[str, Any]:
    body = {"session_id": SID, "text": text, "ui": {"building_id": B, **ui}}
    response = client.post("/api/assistant/turn", json=body)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def test_status_reports_live_assistant_and_no_voice_without_key(
    settings: Settings, datasets_dir: Path
) -> None:
    with make_client(settings, datasets_dir, []) as client:
        status = client.get("/api/assistant/status").json()
    assert status["live"] is True and status["voice"] is False


def test_first_turn_without_session_id_gets_one(
    settings: Settings, datasets_dir: Path, ingest: Any
) -> None:
    with make_client(settings, datasets_dir, ["Hello! How can I help?"]) as client:
        ingest(client)
        response = client.post("/api/assistant/turn", json={"text": "hi", "ui": {"building_id": B}})
    assert response.status_code == 200, response.text
    assert len(response.json()["session_id"]) >= 8


def test_nearest_exit_from_first_floor_ends_at_the_exit_door(
    settings: Settings, datasets_dir: Path, ingest: Any
) -> None:
    script = [
        update(start={"kind": "space", "id": OFFICE_F1}),
        ("find_nearest", {"category": "exit", "step_free": None, "and_route": True}),
        "The nearest exit is downstairs; follow the directions on the map.",
    ]
    with make_client(settings, datasets_dir, script) as client:
        ingest(client)
        out = turn(client, "I'm in Office 1.04. Take me to the nearest exit.")
        building = client.get(f"/api/buildings/{B}/model").json()
    route = out["ui"]["route"]  # the RouteResponse itself (contract), not a wrapper
    assert route["status"] == "ok" and route["mode"] == "official"
    exits = {o["id"]: o for o in building["openings"] if "outside" in o["space_ids"]}
    assert route["target"]["kind"] == "opening" and route["target"]["id"] in exits
    door = exits[route["target"]["id"]]["position"]
    last = route["waypoints"][-1]
    assert last["kind"] == "target" and last["position"] == pytest.approx(door)
    assert route["path"][-1]["points"][-1] == pytest.approx(door)
    # Multi-floor: starts on F1, ends on F0, with a stairs/lift transition between them.
    assert route["start"]["floor_id"].endswith("-F1") and route["target"]["floor_id"].endswith(
        "-F0"
    )
    assert any(t["kind"] in {"stairs", "lift"} for t in route["transitions"])
    # Phase B contract: the waypoints' space sequence is the route's space_path.
    spaces: list[str] = []
    for point in route["waypoints"]:
        if not spaces or spaces[-1] != point["space_id"]:
            spaces.append(point["space_id"])
    assert spaces == route["space_path"]
    assert out["phases"][-1] == "Showing directions"
    # Directions shown to people carry no element ids; the router's own lines stay available.
    assert route["directions"] and not any(B in line for line in route["directions"])
    assert any(B in line for line in route["directions_raw"])


def test_ambiguous_follow_up_ends_with_a_question_not_a_limit_message(
    settings: Settings, datasets_dir: Path, ingest: Any
) -> None:
    loop = [
        ("find_places", {"query": f"connection {i}", "kind": None, "floor_level": None})
        for i in range(MAX_ROUNDS)
    ]
    with make_client(settings, datasets_dir, loop) as client:
        ingest(client)
        out = turn(client, "And the connection from there, the bigger one?")
    assert out["status"] == "budget_reached"
    assert out["reply"].endswith("?") and "budget" not in out["reply"].lower()
    assert "error" not in out["reply"].lower()


def test_confirmed_closure_is_the_one_scenario_for_all_consumers(
    settings: Settings, datasets_dir: Path, ingest: Any
) -> None:
    script = [("propose_change", {"action": "close", "element_id": LIFT}), "Shall I close it?"]
    with make_client(settings, datasets_dir, script) as client:
        ingest(client)
        before = client.get(f"/api/nav/{B}/state").json()
        out = turn(client, "Close the office lift.")
        proposal = out["ui"]["proposal"]
        # Proposing changes nothing.
        assert client.get(f"/api/nav/{B}/state").json()["closed"] == []
        confirmed = client.post(
            "/api/assistant/confirm",
            json={"session_id": SID, "proposal_id": proposal["proposal_id"], "approved": True},
        ).json()
        assert confirmed["status"] == "applied"
        state = client.get(f"/api/nav/{B}/state").json()
        assert state["closed"] == [LIFT] and state["revision"] == before["revision"] + 1

        # The navgraph agent (team /chat engine), the served model and the graph overlay see the
        # same closure; topology edits are not offered there.
        navigator = client.app.state.navigators.get(B)  # type: ignore[attr-defined]
        assert LIFT in json.loads(navigator.call("building_overview", {}))["closed"]
        tools = {t["name"] for t in navigator.tool_schemas()}
        assert "set_closed" in tools and "add_door" not in tools
        blocked = json.loads(navigator.call("add_door", {"first": "a", "second": "b"}))
        assert blocked["error"] == "not_available"
        model = client.get(f"/api/buildings/{B}/model").json()
        assert next(c for c in model["vertical_connectors"] if c["id"] == LIFT)["closed"] is True
        graph = client.get(f"/api/buildings/{B}/graph").json()
        assert LIFT in graph["graph"]["closed"]

        # A closure made by the navgraph agent is written through to the one scenario.
        corridor = f"{B}-F1-S001"
        navigator.call("set_closed", {"element": corridor, "closed": True})
        shared = client.get(f"/api/nav/{B}/state").json()
        assert shared["closed"] == sorted([LIFT, corridor])
        assert shared["revision"] == state["revision"] + 1
        navigator.call("set_closed", {"element": corridor, "closed": False})
        state = client.get(f"/api/nav/{B}/state").json()
        assert state["closed"] == [LIFT]

        # A step-free route from F1 now has no lift: the router says so, nothing is invented.
        route = client.post(
            f"/api/nav/{B}/route",
            json={
                "mode": "official",
                "start": {"kind": "space", "id": OFFICE_F1},
                "target": {"kind": "space", "id": f"{B}-F0-S001"},
                "step_free": True,
            },
        )
        assert route.status_code in {200, 422}
        if route.status_code == 200:
            assert route.json()["reachable"] is False

        # Stale revision is refused; reopening with the current one works.
        stale = client.post(
            f"/api/nav/{B}/scenario",
            json={"action": "open", "element_id": LIFT, "expected_revision": before["revision"]},
        )
        assert stale.status_code == 409
        reopened = client.post(
            f"/api/nav/{B}/scenario",
            json={"action": "open", "element_id": LIFT, "expected_revision": state["revision"]},
        ).json()
        assert reopened["closed"] == []
        assert LIFT not in json.loads(navigator.call("building_overview", {}))["closed"]


def test_offline_assistant_is_explicit_and_manual_routing_still_works(
    settings: Settings, datasets_dir: Path, ingest: Any
) -> None:
    settings.datasets_dir = datasets_dir
    with TestClient(create_app(settings)) as client:  # no key: assistant offline
        ingest(client)
        assert client.get("/api/assistant/status").json()["live"] is False
        response = client.post(
            "/api/assistant/turn", json={"session_id": SID, "text": "hi", "ui": {"building_id": B}}
        )
        assert response.status_code == 503 and "offline" in response.json()["detail"]
        route = client.post(
            f"/api/nav/{B}/route",
            json={
                "mode": "official",
                "start": {"kind": "space", "id": OFFICE_F1},
                "target": {"kind": "asset", "id": f"{B}-A00001"},
            },
        ).json()
        assert route["status"] == "ok" and route["length_m"] > 0


def session_state(transport: Any) -> dict[str, Any]:
    """The <session_state> JSON the model saw in its last request."""
    content = transport.requests[-1]["messages"][-1]["content"]
    text = content if isinstance(content, str) else json.dumps(content)
    start = text.index("<session_state>") + len("<session_state>")
    return dict(
        json.loads(text[start : text.index("</session_state>")].strip().replace('\\"', '"'))
    )


def test_d1_cleared_location_is_not_used_as_start(
    settings: Settings, datasets_dir: Path, ingest: Any
) -> None:
    exit_route = ("find_nearest", {"category": "exit", "step_free": None, "and_route": True})
    script = ["Noted.", exit_route, "Where are you right now?"]
    with make_client(settings, datasets_dir, script) as client:
        ingest(client)
        turn(client, "Hi", start={"kind": "space", "id": OFFICE_F1})
        out = turn(client, "Nearest exit please.", start=None)  # location cleared in the UI
    assert out["state"]["start"] is None
    assert "route" not in out["ui"] or out["ui"].get("route") is None
    assert out["tools"][0]["status"] == "error"  # missing_start: ask, no route from Office 1.04


def test_d2_inspected_item_reaches_the_model_context(
    settings: Settings, datasets_dir: Path, ingest: Any
) -> None:
    cnc = f"{B}-A00001"
    settings.datasets_dir = datasets_dir
    transport = Scripted(["That is CNC mill H-A1-A."])
    with TestClient(create_app(settings, assistant_transport=transport)) as client:
        ingest(client)
        out = turn(client, "What am I looking at?", inspected={"kind": "asset", "id": cnc})
    seen = session_state(transport)
    assert seen["inspected"]["name"] == "CNC mill H-A1-A"
    assert out["state"]["start"] is None and out["state"]["destination"] is None


def test_d3_exact_map_point_start_is_used_or_explicitly_refused(
    settings: Settings, datasets_dir: Path, ingest: Any
) -> None:
    script = [
        (
            "plan_route",
            {
                "start": None,
                "destination": {"kind": "space", "id": f"{B}-F0-S001"},
                "mode": None,
                "step_free": None,
                "assume_closed": [],
            },
        ),
        "Here is the way.",
        "That point is not inside a room. Where are you?",
    ]
    settings.datasets_dir = datasets_dir
    transport = Scripted(script)
    with TestClient(create_app(settings, assistant_transport=transport)) as client:
        ingest(client)
        model = client.get(f"/api/buildings/{B}/model").json()
        office = next(s for s in model["spaces"] if s["id"] == OFFICE_F1)
        xs = [p[0] for p in office["polygon"]]
        ys = [p[1] for p in office["polygon"]]
        point = [sum(xs) / len(xs), sum(ys) / len(ys)]
        start = {"kind": "point", "space_id": OFFICE_F1, "position": point}
        out = turn(client, "Take me to the corridor.", start=start)
        assert out["ui"]["route"]["start"]["kind"] == "point"
        assert out["state"]["start"]["kind"] == "point"
        bad = {"kind": "point", "space_id": "nope", "position": [1, 2]}
        out = turn(client, "And from here?", start=bad)
    assert out["state"]["start"] is None  # no silent fallback to the previous point
    assert "could not be used" in session_state(transport)["ui_note"]


def test_navgraph_asset_edit_is_one_building_for_every_consumer(
    settings: Settings, datasets_dir: Path, ingest: Any
) -> None:
    """Alex's chat edits (move a machine) must not leave AriNav routers on the old building."""
    cnc = f"{B}-A00001"
    with make_client(settings, datasets_dir, []) as client:
        ingest(client)
        state = client.post(
            f"/api/nav/{B}/scenario",
            json={"action": "close", "element_id": LIFT, "expected_revision": 0},
        ).json()
        navigator = client.app.state.navigators.get(B)  # type: ignore[attr-defined]
        moved = json.loads(navigator.call("move_asset", {"asset": cnc, "to_space": OFFICE_F1}))
        assert "error" not in moved
        served = client.get(f"/api/buildings/{B}/model").json()
        nav = client.app.state.navigation  # type: ignore[attr-defined]
        assert next(a for a in served["assets"] if a["id"] == cnc)["space_id"] == OFFICE_F1
        assert next(a for a in nav.building(B)["assets"] if a["id"] == cnc)["space_id"] == OFFICE_F1
        after = client.get(f"/api/nav/{B}/state").json()
        assert after["closed"] == [LIFT]  # the closure survives the new building version
        assert after["revision"] > state["revision"]
        route = client.post(
            f"/api/nav/{B}/route",
            json={
                "mode": "official",
                "start": {"kind": "space", "id": f"{B}-F0-S001"},
                "target": {"kind": "asset", "id": cnc},
            },
        ).json()
        assert route["target"]["space_id"] == OFFICE_F1


def test_c02_c03_wheelchair_with_closed_lift_never_gets_stairs(
    settings: Settings, datasets_dir: Path, ingest: Any
) -> None:
    """Night task C02/C03: a wheelchair user, the only lift closed, destination downstairs. The
    model even tries to drop step_free in tool arguments; the constraint must hold through new
    turns, a mode switch and the route's own constraints (used by guidance reroutes)."""
    corridor = {"kind": "space", "id": f"{B}-F0-S001"}
    script = [
        update(start={"kind": "space", "id": OFFICE_F1}, step_free=True),
        "Noted, step-free.",
        (
            "plan_route",
            {
                "start": None,
                "destination": corridor,
                "mode": None,
                "step_free": False,
                "assume_closed": [],
            },
        ),
        "There is no step-free route right now.",
        ("find_nearest", {"category": "exit", "step_free": False, "and_route": True}),
        "No step-free way out at the moment.",
    ]
    settings.datasets_dir = datasets_dir
    transport = Scripted(script)
    with TestClient(create_app(settings, assistant_transport=transport)) as client:
        ingest(client)
        client.post(
            f"/api/nav/{B}/scenario",
            json={"action": "close", "element_id": LIFT, "expected_revision": 0},
        )
        turn(client, "I use a wheelchair and I'm in Office 1.04.")
        out = turn(client, "Take me to the ground floor corridor.", mode="precision")
        tool = out["tools"][0]
        assert out["state"]["step_free"] is True
        assert tool["input"]["step_free"] is False  # the model tried to loosen it
        assert tool["output"].get("status") != "ok" or tool["output"]["reachable"] is False
        assert "Never suggest stairs" in tool["output"]["constraint"]
        route = out["ui"].get("route")
        assert not route or not any(t["kind"] == "stairs" for t in route["transitions"])
        out = turn(client, "Then the nearest exit.")
        assert out["state"]["step_free"] is True
        nearest = out["tools"][0]["output"]
        routed = nearest.get("route_to_closest") or {}
        assert not routed or routed.get("status") != "ok" or routed.get("reachable") is False
        # The route API echoes the constraint it honoured (guidance reroutes reuse it).
        r = client.post(
            f"/api/nav/{B}/route",
            json={
                "mode": "official",
                "start": {"kind": "space", "id": OFFICE_F1},
                "target": corridor,
                "step_free": True,
            },
        ).json()
        assert r["constraints"]["step_free"] is True and r["reachable"] is False


def test_s01_whats_on_my_right_uses_the_pose_and_view_direction(
    settings: Settings, datasets_dir: Path, ingest: Any
) -> None:
    script = [
        ("describe_surroundings", {"direction": "right"}),
        "The door to the corridor is on your right.",
        ("describe_surroundings", {"direction": "right"}),
        "Which way are you facing?",
    ]
    with make_client(settings, datasets_dir, script) as client:
        ingest(client)
        pose = {
            "floor_id": f"{B}-F1",
            "space_id": OFFICE_F1,
            "position": [2.5, 2.7],
            "heading_deg": 90,
        }
        out = turn(client, "What's on my right?", pose=pose)
        items = out["tools"][0]["output"]["items"]
        assert items and all("right" in (i["side"], i.get("wall_side")) for i in items)
        assert any(i["kind"] == "door" for i in items)
        out = turn(client, "And now?", pose=None)  # pose cleared: must ask, not guess
        assert out["tools"][0]["status"] == "error"


def test_stated_width_binds_official_routes_and_the_ui_gets_the_same_constraint(
    settings: Settings, datasets_dir: Path, ingest: Any
) -> None:
    hall, lab = f"{B}-F0-S001", f"{B}-F0-S023"
    route = {"start": None, "destination": None, "mode": None, "step_free": None}
    script = [
        update(start={"kind": "space", "id": hall}, width_m=1.0),
        ("plan_route", route | {"destination": {"kind": "space", "id": lab}, "assume_closed": []}),
        "That door is too narrow.",
        ("plan_route", route | {"destination": {"kind": "space", "id": lab}, "assume_closed": []}),
        "Still too narrow.",
    ]
    with make_client(settings, datasets_dir, script) as client:
        ingest(client)
        out = turn(client, "I'm 1 m wide and in the hall, can I get to the lab?")
        planned = out["tools"][1]["output"]
        assert planned["reachable"] is False
        check = planned["door_width_check"]
        assert check["required_m"] == 1.0 and "corridors" in check["scope"]
        assert check["too_narrow_doors_on_the_only_route"][0]["width_m"] == 0.9
        assert out["ui"]["route_request"]["min_door_width_m"] == 1.0
        assert out["ui"]["settings"]["door_width_m"] == 1.0
        # The route the UI shows is the assistant's own, with the constraint for reroutes.
        assert out["ui"]["route"]["constraints"]["min_door_width_m"] == 1.0
        # Kept for follow-ups without restating it.
        out = turn(client, "And now?")
        assert out["tools"][0]["output"]["door_width_check"]["required_m"] == 1.0
        assert out["state"]["door_width_checked_m"] == 1.0

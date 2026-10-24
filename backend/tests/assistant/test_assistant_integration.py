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

        # The navgraph agent (team /chat engine) sees the same closure and cannot write its own.
        navigator = client.app.state.navigators.get(B)  # type: ignore[attr-defined]
        assert LIFT in json.loads(navigator.call("building_overview", {}))["closed"]
        assert "set_closed" not in {t["name"] for t in navigator.tool_schemas()}
        refused = json.loads(navigator.call("set_closed", {"element": LIFT, "closed": False}))
        assert refused["error"] == "read_only"
        # The graph overlay shows the scenario closure too.
        graph = client.get(f"/api/buildings/{B}/graph").json()
        assert LIFT in graph["graph"]["closed"]

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

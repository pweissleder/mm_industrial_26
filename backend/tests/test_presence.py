"""Multi-user foundation: idempotent building open, profile leases, saved positions and
stale-version protection for building edits."""

import json
import shutil
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import BACKEND_DIR, Settings
from app.domain.building import BuildingFile
from app.main import create_app
from app.navigation.scenario_sync import ScenarioNavgraphNavigator
from app.presence import PositionStore, SessionLeases
from app.presence.sessions import ProfileOccupiedError, SessionExpiredError
from app.storage import InMemoryBuildingRepository, StaleBuildingError

DATASET = "proc-factory-dev-003"
SAMPLE = BACKEND_DIR / "data" / "datasets" / DATASET
ROOM = f"{DATASET}-F0-S001"  # x in [-14.5, 0], y in [0, 17.1]
OTHER_ROOM = f"{DATASET}-F0-S002"
HEADER = "X-AriNav-Session"


@pytest.fixture
def client(settings: Settings, tmp_path: Path) -> Iterator[TestClient]:
    datasets = tmp_path / "datasets"
    (datasets / DATASET).mkdir(parents=True)
    shutil.copy2(SAMPLE / "building.json", datasets / DATASET)
    people = tmp_path / "profiles.json"
    people.write_text(
        json.dumps(
            [
                {"id": "alex", "name": "Alex Berger", "color": "#2563EB"},
                {"id": "sam", "name": "Sam Okafor", "color": "#16a34a", "initials": "SO"},
            ]
        )
    )
    settings.datasets_dir = datasets
    settings.profiles_file = people
    with TestClient(create_app(settings)) as c:
        yield c


def _claim(client: TestClient, profile: str, token: str | None = None) -> dict:
    headers = {HEADER: token} if token else {}
    return client.post("/api/user-sessions", json={"profile_id": profile}, headers=headers).json()


def _place(client: TestClient, token: str, endpoint: dict, **extra: object):
    body = {"building_id": DATASET, "endpoint": endpoint, **extra}
    return client.put("/api/user-sessions/current/position", json=body, headers={HEADER: token})


# ------------------------------------------------------------ opening a building


def test_open_keeps_live_state_and_ingest_reloads(client: TestClient) -> None:
    first = client.post(f"/api/datasets/{DATASET}/open").json()
    assert first["loaded"] is True and first["building_id"] == DATASET
    state = client.get(f"/api/nav/{DATASET}/state").json()
    closed = client.post(
        f"/api/nav/{DATASET}/scenario",
        json={"action": "close", "element_id": OTHER_ROOM, "expected_revision": state["revision"]},
    ).json()
    assert OTHER_ROOM in closed["closed"]

    again = client.post(f"/api/datasets/{DATASET}/open").json()
    assert again["loaded"] is False and again["replaced"] is False
    assert OTHER_ROOM in client.get(f"/api/nav/{DATASET}/state").json()["closed"]

    client.post("/api/ingestions", json={"dataset": DATASET})  # the explicit reload
    assert client.get(f"/api/nav/{DATASET}/state").json()["closed"] == []


def test_open_unknown_dataset_is_404(client: TestClient) -> None:
    assert client.post("/api/datasets/nope/open").status_code == 404
    assert client.post("/api/datasets/..%2Fx/open").status_code == 404


def test_simultaneous_first_opens_load_once(client: TestClient) -> None:
    results: list[bool] = []
    barrier = threading.Barrier(6)

    def open_it() -> None:
        barrier.wait()
        results.append(client.post(f"/api/datasets/{DATASET}/open").json()["loaded"])

    threads = [threading.Thread(target=open_it) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [False] * 5 + [True]


# ------------------------------------------------------------ profiles and leases


def test_directory_lists_profiles_and_occupancy(client: TestClient) -> None:
    before = client.get("/api/users").json()
    assert [p["id"] for p in before["profiles"]] == ["alex", "sam"]
    assert before["profiles"][0]["initials"] == "AB" and before["profiles"][0]["color"] == "#2563eb"
    assert not any(p["occupied"] for p in before["profiles"])
    assert [p["building_id"] for p in before["profiles"]] == [None, None]

    _claim(client, "alex")
    occupied = {p["id"]: p["occupied"] for p in client.get("/api/users").json()["profiles"]}
    assert occupied == {"alex": True, "sam": False}


def test_one_session_per_profile_and_reload_reclaims(client: TestClient) -> None:
    session = _claim(client, "alex")
    assert session["profile"]["id"] == "alex" and session["presence"] is None

    taken = client.post("/api/user-sessions", json={"profile_id": "alex"})
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "profile_occupied"

    reloaded = _claim(client, "alex", session["token"])
    assert reloaded["token"] == session["token"]
    assert reloaded["generation"] == session["generation"]

    assert client.post("/api/user-sessions", json={"profile_id": "zed"}).status_code == 404


def test_release_frees_profile_and_old_token_cannot_write(client: TestClient) -> None:
    session = _claim(client, "alex")
    client.post(f"/api/datasets/{DATASET}/open")
    assert _place(client, session["token"], {"kind": "space", "id": ROOM}).status_code == 200

    response = client.delete("/api/user-sessions/current", headers={HEADER: session["token"]})
    assert response.status_code == 204
    users = client.get(f"/api/buildings/{DATASET}/users").json()["users"]
    assert [(u["profile"]["id"], u["status"]) for u in users] == [("alex", "offline")]

    newcomer = _claim(client, "alex")
    assert newcomer["token"] != session["token"]
    assert newcomer["generation"] == session["generation"] + 1
    assert newcomer["presence"]["position"]["endpoint"] == {"kind": "space", "id": ROOM}

    stale = _place(client, session["token"], {"kind": "space", "id": OTHER_ROOM})
    assert stale.status_code == 401 and stale.json()["error"]["code"] == "session_expired"
    beat = client.post("/api/user-sessions/current/heartbeat", headers={HEADER: session["token"]})
    assert beat.status_code == 401


def test_lease_expiry_frees_the_profile() -> None:
    now = [0.0]
    leases = SessionLeases(30, clock=lambda: now[0])
    first = leases.claim("alex")
    now[0] = 20
    leases.heartbeat(first.token)  # extends to 50
    now[0] = 45
    with pytest.raises(ProfileOccupiedError):
        leases.claim("alex")
    now[0] = 51
    assert leases.occupied() == set()
    with pytest.raises(SessionExpiredError):
        leases.current(first.token)
    second = leases.claim("alex", first.token)  # an expired token is a fresh claim
    assert second.token != first.token and second.generation == 2


# ------------------------------------------------------------ positions


def test_set_and_clear_position(client: TestClient) -> None:
    client.post(f"/api/datasets/{DATASET}/open")
    token = _claim(client, "sam")["token"]
    assert client.get(f"/api/buildings/{DATASET}/users").json()["users"] == []

    placed = _place(
        client,
        token,
        {"kind": "point", "space_id": ROOM, "position": [-3, 4]},
        label="By the window",
        heading=90,
    )
    assert placed.status_code == 200
    body = placed.json()
    assert body["status"] == "online" and body["available"] is True
    assert body["position"]["position"] == [-3.0, 4.0]
    assert body["position"]["floor_id"] == f"{DATASET}-F0"
    assert body["position"]["label"] == "By the window"
    first_revision = body["revision"]

    room = _place(client, token, {"kind": "space", "id": ROOM}).json()
    assert room["revision"] == first_revision + 1
    assert room["position"]["space_id"] == ROOM

    cleared = client.delete("/api/user-sessions/current/position", headers={HEADER: token}).json()
    assert cleared["presence"]["revision"] == first_revision + 2
    # Still in the building (their current location), just without a spot on the map.
    users = client.get(f"/api/buildings/{DATASET}/users").json()["users"]
    assert [(u["profile"]["id"], u["position"]) for u in users] == [("sam", None)]

    again = _place(client, token, {"kind": "space", "id": ROOM}).json()
    assert again["revision"] == first_revision + 3  # revisions never go back after a clear


def test_entering_a_building_without_a_place(client: TestClient) -> None:
    client.post(f"/api/datasets/{DATASET}/open")
    token = _claim(client, "sam")["token"]
    body = {"building_id": DATASET}
    headers = {HEADER: token}
    entered = client.put("/api/user-sessions/current/position", json=body, headers=headers)
    assert entered.status_code == 200 and entered.json()["position"] is None

    placed = _place(client, token, {"kind": "space", "id": ROOM}).json()
    # Entering the building one is already in keeps the place.
    again = client.put("/api/user-sessions/current/position", json=body, headers=headers).json()
    assert again["position"]["space_id"] == ROOM and again["revision"] == placed["revision"]

    missing = client.put(
        "/api/user-sessions/current/position", json={"building_id": "nope"}, headers=headers
    )
    assert missing.status_code == 404


def test_invalid_positions_are_refused(client: TestClient) -> None:
    client.post(f"/api/datasets/{DATASET}/open")
    token = _claim(client, "sam")["token"]
    outside = _place(client, token, {"kind": "point", "space_id": ROOM, "position": [50, 50]})
    assert outside.status_code == 422
    unknown = _place(client, token, {"kind": "space", "id": "nope"})
    assert unknown.status_code == 422 and unknown.json()["error"]["code"] == "unknown_place"
    body = {"building_id": "elsewhere", "endpoint": {"kind": "space", "id": ROOM}}
    other = client.put("/api/user-sessions/current/position", json=body, headers={HEADER: token})
    assert other.status_code == 404
    assert (
        _place(client, token, {"kind": "space", "id": ROOM}, source="teleport").status_code == 422
    )
    assert (
        client.put("/api/user-sessions/current/position", json=body).status_code == 401
    )  # no session header


def test_positions_survive_a_restart(settings: Settings, client: TestClient) -> None:
    client.post(f"/api/datasets/{DATASET}/open")
    token = _claim(client, "alex")["token"]
    _place(client, token, {"kind": "space", "id": ROOM})
    client.close()  # ends the app's lifespan; leases are gone with the process

    with TestClient(create_app(settings)) as restarted:
        # Before the building is open again the position cannot be checked: kept, unavailable.
        users = restarted.get(f"/api/buildings/{DATASET}/users").json()["users"]
        assert [(u["status"], u["available"]) for u in users] == [("offline", False)]

        restarted.post(f"/api/datasets/{DATASET}/open")
        users = restarted.get(f"/api/buildings/{DATASET}/users").json()["users"]
        assert [(u["status"], u["available"]) for u in users] == [("offline", True)]
        # The person is free to claim, and continues from the saved position.
        session = _claim(restarted, "alex")
        assert session["presence"]["position"]["space_id"] == ROOM


def test_position_store_is_monotonic(tmp_path: Path) -> None:
    store = PositionStore(tmp_path / "p.sqlite3")
    assert store.get("a") is None
    assert store.clear("a") is None  # nobody to clear
    one = store.put("a", "b1", {"x": 1})
    cleared = store.clear("a")
    assert cleared is not None and cleared.revision == one.revision + 1
    assert store.get("a") == cleared and cleared.position is None
    two = store.put("a", "b2", {"x": 2})  # one building at a time: b1 no longer has them
    assert two.revision == one.revision + 2
    assert store.in_building("b1") == [] and store.in_building("b2") == [two]
    store.close()
    reopened = PositionStore(tmp_path / "p.sqlite3")
    assert reopened.get("a") == two


# ------------------------------------------------------------ concurrent building edits


def test_repository_refuses_a_save_over_a_newer_version() -> None:
    repository = InMemoryBuildingRepository()
    data = BuildingFile.model_validate_json((SAMPLE / "building.json").read_bytes())
    repository.save(DATASET, data)
    base = repository.get(DATASET).data  # type: ignore[union-attr]
    newer = data.model_copy(deep=True)
    repository.save(DATASET, newer, expected=base)
    with pytest.raises(StaleBuildingError):
        repository.save(DATASET, data.model_copy(deep=True), expected=base)


class _Scenario:
    def closed_elements(self, building_id: str) -> list[str]:
        return []

    def state(self, building_id: str) -> dict:
        return {"revision": 0}

    def change(self, *args: object) -> dict:
        return {}


def test_asset_edit_on_an_outdated_navigator_is_refused() -> None:
    repository = InMemoryBuildingRepository()
    data = BuildingFile.model_validate_json((SAMPLE / "building.json").read_bytes())
    repository.save(DATASET, data)
    stored = repository.get(DATASET)
    assert stored is not None
    first = ScenarioNavgraphNavigator(stored.data, _Scenario(), repository)
    second = ScenarioNavgraphNavigator(stored.data, _Scenario(), repository)

    added = json.loads(first.call("add_asset", {"name": "Bench", "type": "bench", "space": "0.23"}))
    assert "added" in added
    # `second` still works on the version before that edit; its save must not erase it.
    refused = json.loads(
        second.call("add_asset", {"name": "Crate", "type": "crate", "space": "0.23"})
    )
    assert refused["error"] == "stale_building"
    names = {a.name for a in repository.get(DATASET).data.assets}  # type: ignore[union-attr]
    assert "Bench" in names and "Crate" not in names

    # The navigator that saved keeps working on its own (now stored) version.
    again = json.loads(first.call("add_asset", {"name": "Shelf", "type": "shelf", "space": "0.23"}))
    assert "added" in again


def test_configured_people_start_at_their_place(settings: Settings, tmp_path: Path) -> None:
    datasets = tmp_path / "datasets"
    (datasets / DATASET).mkdir(parents=True)
    shutil.copy2(SAMPLE / "building.json", datasets / DATASET)
    people = tmp_path / "profiles.json"
    start = {"dataset": DATASET, "endpoint": {"kind": "space", "id": ROOM}, "label": "Hall"}
    people.write_text(
        json.dumps(
            [
                {"id": "alex", "name": "Alex Berger", "color": "#2563eb", "start": start},
                {"id": "sam", "name": "Sam Okafor", "color": "#16a34a"},
                {
                    "id": "kim",
                    "name": "Kim Novak",
                    "color": "#0891b2",
                    "start": {"dataset": "missing", "endpoint": {"kind": "space", "id": ROOM}},
                },
            ]
        )
    )
    settings.datasets_dir = datasets
    settings.profiles_file = people

    with TestClient(create_app(settings)) as client:
        users = client.get(f"/api/buildings/{DATASET}/users").json()["users"]
        assert [(u["profile"]["id"], u["status"], u["available"]) for u in users] == [
            ("alex", "offline", True)
        ]
        assert users[0]["position"]["label"] == "Hall"
        assert "start" not in client.get("/api/users").json()["profiles"][0]
        token = _claim(client, "alex")["token"]
        client.delete("/api/user-sessions/current/position", headers={HEADER: token})

    with TestClient(create_app(settings)) as client:  # a cleared position is not seeded again
        users = client.get(f"/api/buildings/{DATASET}/users").json()["users"]
        assert [(u["profile"]["id"], u["position"]) for u in users] == [("alex", None)]


def test_shipped_profiles_place_three_people(settings: Settings) -> None:
    settings.datasets_dir = BACKEND_DIR / "data" / "datasets"
    settings.profiles_file = BACKEND_DIR / "data" / "people" / "profiles.json"
    with TestClient(create_app(settings)) as client:
        assert len(client.get("/api/users").json()["profiles"]) == 3
        users = client.get(f"/api/buildings/{DATASET}/users").json()["users"]
        assert sorted(u["profile"]["id"] for u in users) == ["alex", "jordan", "sam"]
        assert all(u["available"] for u in users)


def test_live_updates_push_movement_and_status(client: TestClient) -> None:
    client.post(f"/api/datasets/{DATASET}/open")
    watcher = _claim(client, "alex")["token"]
    _place(client, watcher, {"kind": "space", "id": ROOM})

    with client.websocket_connect(f"/api/live?building_id={DATASET}") as live:
        first = live.receive_json()
        assert first["type"] == "users" and [u["profile"]["id"] for u in first["users"]] == ["alex"]

        walker = _claim(client, "sam")["token"]  # Sam had no place: no change here yet
        walked = _place(
            client,
            walker,
            {"kind": "point", "space_id": ROOM, "position": [0.8, 4]},  # just past the wall
            moving=True,
            source="simulation",
        )
        assert walked.status_code == 200  # tolerated while walking (doorways)
        update = live.receive_json()
        sam = next(u for u in update["users"] if u["profile"]["id"] == "sam")
        assert sam["status"] == "online" and sam["position"]["moving"] is True

        client.delete("/api/user-sessions/current", headers={HEADER: walker})
        update = live.receive_json()
        sam = next(u for u in update["users"] if u["profile"]["id"] == "sam")
        assert sam["status"] == "offline"


def test_standing_still_outside_the_room_is_refused(client: TestClient) -> None:
    client.post(f"/api/datasets/{DATASET}/open")
    token = _claim(client, "sam")["token"]
    spot = {"kind": "point", "space_id": ROOM, "position": [0.8, 4]}
    assert _place(client, token, spot).status_code == 422


# ------------------------------------------------------------ people as places


SAM_SPOT = [-3.0, 4.0]  # inside ROOM


def _people_here(client: TestClient) -> str:
    """Alex in OTHER_ROOM's room, Sam at SAM_SPOT; returns Alex's token."""
    client.post(f"/api/datasets/{DATASET}/open")
    sam = _claim(client, "sam")["token"]
    spot = {"kind": "point", "space_id": ROOM, "position": SAM_SPOT}
    assert _place(client, sam, spot, label="By the press").status_code == 200
    alex = _claim(client, "alex")["token"]
    _place(client, alex, {"kind": "space", "id": OTHER_ROOM})
    return alex


def test_people_are_found_by_name(client: TestClient) -> None:
    _people_here(client)
    found = client.get(f"/api/nav/{DATASET}/search", params={"q": "Sam"}).json()["results"]
    assert found[0]["kind"] == "user" and found[0]["id"] == "sam"
    assert found[0]["space_id"] == ROOM and found[0]["place"] == "By the press"
    only = client.get(f"/api/nav/{DATASET}/search", params={"q": "okafor", "kind": "person"})
    assert [r["id"] for r in only.json()["results"]] == ["sam"]


def test_route_to_a_person_ends_at_their_spot(client: TestClient) -> None:
    _people_here(client)
    body = {"start": {"kind": "space", "id": OTHER_ROOM}, "target": {"kind": "user", "id": "sam"}}
    route = client.post(f"/api/nav/{DATASET}/route", json=body).json()
    assert route["status"] == "ok"
    assert route["target_person"]["name"] == "Sam Okafor"
    assert route["target"]["position"] == SAM_SPOT
    assert route["path"][-1]["points"][-1] == SAM_SPOT
    # Endpoint-aware: the official room-to-room cost is kept separately.
    assert route["length_m"] != route["official_length_m"]

    to_room = client.post(
        f"/api/nav/{DATASET}/route",
        json={"start": body["start"], "target": {"kind": "space", "id": ROOM}},
    ).json()
    assert to_room["length_m"] == route["official_length_m"]


def test_route_to_someone_not_here_is_explained(client: TestClient) -> None:
    client.post(f"/api/datasets/{DATASET}/open")
    token = _claim(client, "sam")["token"]
    body = {"start": {"kind": "space", "id": ROOM}, "target": {"kind": "user", "id": "sam"}}
    away = client.post(f"/api/nav/{DATASET}/route", json=body).json()
    assert away["error"]["code"] == "person_elsewhere"
    client.put(
        "/api/user-sessions/current/position",
        json={"building_id": DATASET},
        headers={HEADER: token},
    )
    unplaced = client.post(f"/api/nav/{DATASET}/route", json=body).json()
    assert unplaced["error"]["code"] == "person_unplaced"
    nobody = {**body, "target": {"kind": "user", "id": "zed"}}
    assert client.post(f"/api/nav/{DATASET}/route", json=nobody).status_code == 422


def test_people_are_graph_nodes(client: TestClient) -> None:
    _people_here(client)
    graph = client.get(f"/api/buildings/{DATASET}/graph").json()
    sam = next(n for n in graph["nodes"] if n["id"] == "user:sam")
    assert sam["kind"] == "user" and sam["anchor"] == SAM_SPOT
    link = next(e for e in graph["edges"] if "user:sam" in (e["source"], e["target"]))
    assert {link["source"], link["target"]} == {ROOM, "user:sam"}
    assert link["kind"] == "person" and link["cost_m"] > 0


def test_assistant_routes_to_a_person(client: TestClient) -> None:
    _people_here(client)
    assistant = client.app.state.assistant  # type: ignore[attr-defined]
    session = assistant.session("people-test")
    session.state["building_id"] = DATASET
    session.state["start"] = {"kind": "space", "id": OTHER_ROOM}

    found = assistant.tool_find_places(session, {"query": "Sam", "kind": "person"}, ui := {})
    assert found["results"][0]["kind"] == "user" and ui["focus"]["kind"] == "user"

    destination = {"kind": "user", "id": "sam"}
    args = {"start": None, "destination": destination, "mode": None, "step_free": None}
    out = assistant.tool_plan_route(session, {**args, "assume_closed": []}, ui := {})
    assert out["status"] == "ok" and out["to"] == "Sam Okafor"
    assert out["person"]["status"] == "online" and out["person"]["note"] is None
    assert ui["route"]["target"]["position"] == SAM_SPOT
    assert ui["route"]["target_person"]["id"] == "sam"  # the app follows Sam from here
    assert ui["route_request"]["target"] == {"kind": "user", "id": "sam"}

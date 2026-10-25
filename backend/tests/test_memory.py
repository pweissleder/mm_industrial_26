"""Opt-in activity memory: sessions, strict events, recap math, delete/export, isolation."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import memory
from app.config import Settings
from app.memory import MemoryStore


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def app(tmp_path: Path, clock: Clock) -> FastAPI:
    app = FastAPI()
    app.state.settings = Settings(_env_file=None, workspace_dir=tmp_path)
    app.state.memory_store = MemoryStore(tmp_path / "memory", clock=clock)
    app.include_router(memory.router, prefix="/api")
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def new_session(client: TestClient, kind: str = "real") -> str:
    response = client.post("/api/memory/sessions", json={"kind": kind})
    assert response.status_code == 201
    assert response.json()["kind"] == kind
    session_id: str = response.json()["session_id"]
    return session_id


def send(client: TestClient, sid: str, type_: str, data: dict[str, Any] | None = None) -> int:
    body: dict[str, Any] = {"session_id": sid, "type": type_}
    if data is not None:
        body["data"] = data
    return client.post("/api/memory/events", json=body).status_code


def recap(client: TestClient, sid: str) -> dict[str, Any]:
    response = client.get("/api/memory/recap", params={"session_id": sid})
    assert response.status_code == 200
    result: dict[str, Any] = response.json()
    return result


def moved(xy: float, floor: str = "Floor 3") -> dict[str, Any]:
    return {"xy_m": xy, "floor_id": f"F-{floor}", "floor_name": floor}


def test_store_is_created_lazily_under_workspace(tmp_path: Path) -> None:
    app = FastAPI()
    app.state.settings = Settings(_env_file=None, workspace_dir=tmp_path)
    app.include_router(memory.router, prefix="/api")
    with TestClient(app) as client:
        assert not (tmp_path / "memory").exists()
        sid = new_session(client)
    assert (tmp_path / "memory" / f"{sid}.jsonl").is_file()


def test_opt_in_unknown_session_is_404_and_nothing_written(
    client: TestClient, tmp_path: Path
) -> None:
    sid = "x" * 22
    assert send(client, sid, "nav_start") == 404
    assert client.get("/api/memory/recap", params={"session_id": sid}).status_code == 404
    assert client.get(f"/api/memory/sessions/{sid}/export").status_code == 404
    assert not (tmp_path / "memory").exists() or not any((tmp_path / "memory").iterdir())


def test_malformed_session_id_cannot_escape_the_folder(client: TestClient) -> None:
    assert send(client, "../../etc/passwd", "nav_start") == 422
    assert client.get("/api/memory/recap", params={"session_id": "../x"}).status_code == 404


@pytest.mark.parametrize(
    ("type_", "data"),
    [
        ("transcript", {"text": "hi"}),
        ("search_intent", {"text": "x" * 121}),
        ("search_intent", {"text": "printer", "audio": "base64..."}),
        ("moved", {"xy_m": -1, "floor_id": "F3"}),
        ("moved", {"xy_m": 2}),
        ("moved", {"xy_m": "nan", "floor_id": "F3"}),
        ("nav_start", {"image": "x"}),
        ("arrival", {}),
        ("destination_confirmed", {"name": "Lab", "kind": "space", "extra": 1}),
        ("setting_confirmed", {"key": "step_free"}),
    ],
)
def test_events_are_validated(client: TestClient, type_: str, data: dict[str, Any]) -> None:
    sid = new_session(client)
    assert send(client, sid, type_, data) == 422


def test_unknown_top_level_field_rejected(client: TestClient) -> None:
    sid = new_session(client)
    body = {"session_id": sid, "type": "nav_start", "transcript": "full text"}
    assert client.post("/api/memory/events", json=body).status_code == 422


def test_allowed_events_are_stored_with_server_timestamps(client: TestClient) -> None:
    sid = new_session(client)
    assert send(client, sid, "search_intent", {"text": "where can I print"}) == 201
    assert send(client, sid, "destination_confirmed", {"name": "Lab 2.07", "kind": "space"}) == 201
    assert send(client, sid, "setting_confirmed", {"key": "step_free", "value": True}) == 201
    events = client.get(f"/api/memory/sessions/{sid}/export").json()["events"]
    assert [e["type"] for e in events] == [
        "search_intent",
        "destination_confirmed",
        "setting_confirmed",
    ]
    assert all(e["at"].startswith("2026-10-25T12:00:00") for e in events)
    assert events[2]["data"] == {"key": "step_free", "value": True}


def test_recap_counts_only_walked_xy_and_one_arrival_per_navigation(
    client: TestClient, clock: Clock
) -> None:
    sid = new_session(client)
    # Planned and "previewed" only: no movement events exist for that, so nothing counts.
    send(client, sid, "destination_confirmed", {"name": "Exit", "kind": "opening", "id": "O23"})
    send(client, sid, "nav_start")
    clock.advance(30)
    send(client, sid, "moved", moved(12.3))
    send(client, sid, "nav_pause")
    clock.advance(600)  # paused time is excluded
    send(client, sid, "nav_resume")
    clock.advance(30)
    send(client, sid, "moved", moved(13.9, "Ground floor"))
    send(client, sid, "arrival", {"name": "Exit", "floor_name": "Ground floor"})
    send(client, sid, "arrival", {"name": "Exit", "floor_name": "Ground floor"})  # duplicate
    send(client, sid, "moved", moved(99))  # outside a navigation: ignored
    # A cancelled navigation: started, walked a bit, stopped.
    send(client, sid, "nav_start")
    clock.advance(60)
    send(client, sid, "moved", moved(5.25, "Floor 2"))
    send(client, sid, "reroute", {"reason": "scenario_changed"})
    send(client, sid, "nav_stop")

    r = recap(client, sid)
    assert r["walked_m"] == pytest.approx(31.45)
    assert r["places_reached"] == ["Exit"]
    assert r["places_reached_count"] == 1
    assert r["arrivals"] == 1
    assert r["navigations_started"] == 2
    assert r["navigations_completed"] == 1
    assert r["reroutes"] == 1
    assert r["floors_visited"] == ["Floor 3", "Ground floor", "Floor 2"]
    assert r["favourite_destination"] == "Exit"
    assert r["simulated_minutes"] == pytest.approx(2.0)
    assert r["kind"] == "real"
    assert r["synthetic"] is False


def test_favourite_is_most_reached_tie_goes_to_first(client: TestClient) -> None:
    sid = new_session(client)
    for name in ["Lab", "Kitchen", "Kitchen", "Lab", "Office"]:
        send(client, sid, "nav_start")
        send(client, sid, "arrival", {"name": name})
    r = recap(client, sid)
    assert r["places_reached"] == ["Lab", "Kitchen", "Office"]
    assert r["places_reached_count"] == 3
    assert r["arrivals"] == 5
    assert r["favourite_destination"] == "Lab"


def test_empty_session_recap_has_no_invented_numbers(client: TestClient) -> None:
    r = recap(client, new_session(client))
    assert r["walked_m"] == 0
    assert r["places_reached"] == []
    assert r["favourite_destination"] is None
    assert r["simulated_minutes"] == 0
    assert r["event_count"] == 0


def test_delete_removes_file_and_prevents_resurrection(client: TestClient, tmp_path: Path) -> None:
    sid = new_session(client)
    send(client, sid, "nav_start")
    assert client.delete(f"/api/memory/sessions/{sid}").status_code == 204
    assert not (tmp_path / "memory" / f"{sid}.jsonl").exists()
    assert send(client, sid, "arrival", {"name": "Lab"}) == 404
    assert not (tmp_path / "memory" / f"{sid}.jsonl").exists()
    assert client.get("/api/memory/recap", params={"session_id": sid}).status_code == 404
    assert client.delete(f"/api/memory/sessions/{sid}").status_code == 404


def test_export_returns_events_as_json(client: TestClient) -> None:
    sid = new_session(client, "demo")
    send(client, sid, "nav_start")
    send(client, sid, "moved", moved(4.5))
    body = client.get(f"/api/memory/sessions/{sid}/export").json()
    assert body["session_id"] == sid
    assert body["kind"] == "demo"
    assert [e["type"] for e in body["events"]] == ["nav_start", "moved"]
    assert body["events"][1]["data"] == {
        "xy_m": 4.5,
        "floor_id": "F-Floor 3",
        "floor_name": "Floor 3",
    }


@pytest.mark.parametrize(
    ("kind", "label", "synthetic"),
    [
        ("real", "Your session", False),
        ("demo", "Demo session (synthetic)", True),
        ("test", "Test session (not user history)", True),
    ],
)
def test_session_kind_is_labelled(
    client: TestClient, kind: str, label: str, synthetic: bool
) -> None:
    r = recap(client, new_session(client, kind))
    assert (r["kind"], r["label"], r["synthetic"]) == (kind, label, synthetic)


def test_unknown_kind_rejected(client: TestClient) -> None:
    assert client.post("/api/memory/sessions", json={"kind": "admin"}).status_code == 422


def test_sessions_are_isolated(client: TestClient) -> None:
    real = new_session(client, "real")
    demo = new_session(client, "demo")
    assert real != demo
    send(client, demo, "nav_start")
    send(client, demo, "moved", moved(40))
    send(client, demo, "arrival", {"name": "Canteen"})
    send(client, real, "nav_start")
    send(client, real, "moved", moved(3))
    assert recap(client, real)["walked_m"] == 3
    assert recap(client, real)["places_reached"] == []
    assert recap(client, demo)["walked_m"] == 40
    client.delete(f"/api/memory/sessions/{demo}")
    assert recap(client, real)["walked_m"] == 3

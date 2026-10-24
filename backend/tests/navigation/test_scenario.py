"""RAM scenario (closures + revision), stale-revision conflicts and the side-effect-free preview."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.navigation import NavigationService
from app.navigation.errors import HTTP_STATUS, NavigationError
from tests.navigation.conftest import BID, DATASET, DATASETS_DIR, P, space


def test_close_open_reset_with_revision(fresh_nav: NavigationService) -> None:
    nav = fresh_nav
    state = nav.state(BID)
    assert (state["revision"], state["closed"]) == (0, [])
    base = nav.route(BID, {"start": space("F0-S021"), "target": space("F1-S010")})

    closed = nav.change(BID, "close", P + "V01", 0)
    assert (closed["revision"], closed["closed"], closed["changed"]) == (1, [P + "V01"], True)
    lift = nav.route(BID, {"start": space("F0-S021"), "target": space("F1-S010")})
    assert lift["revision"] == 1 and lift["closed"] == [P + "V01"]
    assert [t["kind"] for t in lift["transitions"] if t["kind"] != "door"] == ["lift"]
    assert lift["length_m"] > base["length_m"]

    again = nav.change(BID, "close", P + "V01", 1)  # no-op keeps the revision
    assert (again["revision"], again["changed"]) == (1, False)

    opened = nav.change(BID, "open", P + "V01", 1)
    assert (opened["revision"], opened["closed"]) == (2, [])
    assert (
        nav.route(BID, {"start": space("F0-S021"), "target": space("F1-S010")})["length_m"]
        == base["length_m"]
    )

    nav.change(BID, "close", P + "F0-S016", 2)
    nav.change(BID, "close", P + "F0-O0054", 3)
    reset = nav.change(BID, "reset", None, 4)
    assert (reset["revision"], reset["closed"], reset["changed"]) == (5, [], True)


def test_stale_revision_is_a_conflict(fresh_nav: NavigationService) -> None:
    fresh_nav.change(BID, "close", P + "V01", 0)
    with pytest.raises(NavigationError) as caught:
        fresh_nav.change(BID, "close", P + "V02", 0)
    assert caught.value.code == "revision_conflict"
    assert HTTP_STATUS[caught.value.code] == 409
    assert caught.value.details == {"revision": 1, "expected_revision": 0}
    assert fresh_nav.state(BID)["closed"] == [P + "V01"]  # nothing applied


@pytest.mark.parametrize(
    ("action", "element", "revision", "code"),
    [
        ("close", "not-real", 0, "unknown_place"),
        ("close", None, 0, "unknown_place"),
        ("delete", P + "V01", 0, "invalid_input"),
        ("close", P + "V01", "0", "invalid_input"),
        ("close", P + "V01", True, "invalid_input"),
    ],
)
def test_invalid_scenario_changes_are_atomic(
    fresh_nav: NavigationService, action: str, element: str | None, revision: object, code: str
) -> None:
    with pytest.raises(NavigationError) as caught:
        fresh_nav.change(BID, action, element, revision)  # type: ignore[arg-type]
    assert caught.value.code == code
    assert fresh_nav.state(BID)["revision"] == 0 and fresh_nav.state(BID)["closed"] == []


def test_assume_closed_is_never_stored(fresh_nav: NavigationService) -> None:
    res = fresh_nav.route(
        BID,
        {"start": space("F0-S021"), "target": space("F1-S010"), "assume_closed": [P + "V01"]},
    )
    assert res["assume_closed"] == [P + "V01"] and res["revision"] == 0
    assert fresh_nav.state(BID)["closed"] == []


def test_preview_closure_changes_nothing(fresh_nav: NavigationService) -> None:
    nav = fresh_nav
    before_state = nav.state(BID)
    before_route = nav.route(BID, {"start": space("F0-S021"), "target": space("F0-S030")})
    preview = nav.preview_closure(BID, [P + "F0-S016"])
    assert preview["changed"] is False and preview["revision"] == 0
    assert preview["measured_from"] == "the building entrances"
    assert preview["rooms_unreachable_after"] == len(preview["unreachable_ids"]) > 0
    assert P + "F0-S021" in preview["unreachable_ids"]
    assert P + "F0-S016" not in preview["unreachable_ids"]
    assert preview["closed_for_preview"][0]["kind"] == "space"
    assert nav.state(BID) == before_state
    assert (
        nav.route(BID, {"start": space("F0-S021"), "target": space("F0-S030")})["length_m"]
        == before_route["length_m"]
    )
    from_room = nav.preview_closure(BID, [P + "V01"], origin=space("F0-S021"))
    assert from_room["rooms_unreachable_after"] == 0  # the lift still serves floor 1
    assert from_room["rooms_longer_by_more_than_1_m"] > 0
    assert nav.state(BID) == before_state
    with pytest.raises(NavigationError):
        nav.preview_closure(BID, [])
    with pytest.raises(NavigationError):
        nav.preview_closure(BID, ["nope"])


def test_reingest_drops_closures_and_moves_revision(fresh_nav: NavigationService) -> None:
    from app.ingestion.service import ingest_dataset

    fresh_nav.change(BID, "close", P + "V01", 0)
    ingest_dataset(fresh_nav.repository, DATASETS_DIR, DATASET)  # type: ignore[arg-type]
    state = fresh_nav.state(BID)
    assert state["closed"] == [] and state["revision"] == 2


# ---------------------------------------------------------------- HTTP contract (409)


@pytest.fixture
def api(settings: Settings) -> Iterator[TestClient]:
    settings.datasets_dir = DATASETS_DIR
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/ingestions", json={"dataset": DATASET}).status_code == 201
        yield client


def test_scenario_endpoint_revision_and_409(api: TestClient) -> None:
    url = f"/api/nav/{BID}/scenario"
    ok = api.post(url, json={"action": "close", "element_id": P + "V01", "expected_revision": 0})
    assert ok.status_code == 200 and ok.json()["revision"] == 1
    stale = api.post(url, json={"action": "close", "element_id": P + "V02", "expected_revision": 0})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "revision_conflict"
    state = api.get(f"/api/nav/{BID}/state").json()
    assert (state["revision"], state["closed"]) == (1, [P + "V01"])

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import BACKEND_DIR, Settings
from app.main import create_app

DATASET = "proc-factory-dev-003"
SAMPLE = BACKEND_DIR / "data" / "datasets" / DATASET


@pytest.fixture
def datasets_dir(tmp_path: Path) -> Path:
    (tmp_path / DATASET).mkdir()
    shutil.copy2(SAMPLE / "building.json", tmp_path / DATASET)
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "building.json").write_text(json.dumps({"building": {"id": "x"}}))
    return tmp_path


@pytest.fixture
def client(settings: Settings, datasets_dir: Path) -> Iterator[TestClient]:
    settings.datasets_dir = datasets_dir
    with TestClient(create_app(settings)) as c:
        yield c


def test_lists_available_datasets(client: TestClient) -> None:
    assert client.get("/api/datasets").json() == {"datasets": ["broken", DATASET]}


def test_ingests_dataset_and_exposes_building(client: TestClient) -> None:
    response = client.post("/api/ingestions", json={"dataset": DATASET})

    assert response.status_code == 201
    summary = response.json()
    assert summary == {
        "dataset": DATASET,
        "building_id": DATASET,
        "building_name": "Fertigung Ost 27",
        "floors": 2,
        "spaces": summary["spaces"],
        "openings": summary["openings"],
        "vertical_connectors": summary["vertical_connectors"],
        "assets": summary["assets"],
        "replaced": False,
    }
    assert summary["spaces"] > 0 and summary["assets"] > 0

    detail = client.get(f"/api/buildings/{DATASET}").json()
    assert [f["level"] for f in detail["floors"]] == [0, 1]
    assert detail["space_count"] == summary["spaces"]
    assert detail["asset_count"] == summary["assets"]
    assert [b["id"] for b in client.get("/api/buildings").json()] == [DATASET]


def test_reingesting_replaces_building(client: TestClient) -> None:
    first = client.post("/api/ingestions", json={"dataset": DATASET}).json()
    second = client.post("/api/ingestions", json={"dataset": DATASET}).json()

    assert second["replaced"] is True
    assert client.get(f"/api/buildings/{DATASET}").json()["asset_count"] == first["assets"]


@pytest.mark.parametrize("dataset", ["does-not-exist", "../datasets", "/etc"])
def test_unknown_or_unsafe_dataset_is_404(client: TestClient, dataset: str) -> None:
    assert client.post("/api/ingestions", json={"dataset": dataset}).status_code == 404


def test_invalid_building_file_is_422(client: TestClient) -> None:
    response = client.post("/api/ingestions", json={"dataset": "broken"})

    assert response.status_code == 422
    assert client.get("/api/buildings").json() == []


def test_unknown_building_is_404(client: TestClient) -> None:
    assert client.get("/api/buildings/nope").status_code == 404

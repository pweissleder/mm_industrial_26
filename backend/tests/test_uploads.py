import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import BACKEND_DIR, Settings
from app.main import create_app

SAMPLE = BACKEND_DIR / "data" / "datasets" / "proc-factory-dev-003" / "building.json"


@pytest.fixture
def datasets_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def client(settings: Settings, datasets_dir: Path) -> Iterator[TestClient]:
    settings.datasets_dir = datasets_dir
    settings.max_upload_bytes = 1_000_000
    with TestClient(create_app(settings)) as c:
        yield c


def test_upload_creates_ingestible_dataset(client: TestClient, datasets_dir: Path) -> None:
    response = client.post(
        "/api/datasets/my-plant/files",
        files=[
            ("files", ("building.json", SAMPLE.read_bytes(), "application/json")),
            ("files", ("floor_0.svg", b"<svg/>", "image/svg+xml")),
        ],
    )

    assert response.status_code == 201
    assert response.json()["ingestible"] is True
    assert [f["name"] for f in response.json()["files"]] == ["building.json", "floor_0.svg"]
    assert (datasets_dir / "my-plant" / "floor_0.svg").read_bytes() == b"<svg/>"
    assert client.get("/api/datasets").json() == {"datasets": ["my-plant"]}
    assert client.post("/api/ingestions", json={"dataset": "my-plant"}).status_code == 201

    model = client.get("/api/buildings/proc-factory-dev-003/model").json()
    assert model["building"]["name"] == "Fertigung Ost 27"
    assert len(model["spaces"]) > 0

    graph = client.get("/api/buildings/proc-factory-dev-003/graph").json()
    assert len(graph["nodes"]) == len(model["spaces"]) + 1  # plus "outside"
    assert graph["edges"]


def test_lists_and_serves_files(client: TestClient) -> None:
    client.post("/api/datasets/d1/files", files=[("files", ("model.glb", b"glTF", "model/gltf"))])

    assert client.get("/api/datasets/d1/files").json() == {
        "dataset": "d1",
        "files": [{"name": "model.glb", "size_bytes": 4}],
    }
    assert client.get("/api/datasets/d1/files/model.glb").content == b"glTF"
    assert client.get("/api/datasets/d1/files/missing.glb").status_code == 404
    assert client.get("/api/datasets/nope/files").status_code == 404


@pytest.mark.parametrize(
    ("dataset", "filename"),
    [("..evil", "a.json"), ("ok", "run.sh"), ("ok", ".hidden.json")],
)
def test_rejects_bad_names(
    client: TestClient, datasets_dir: Path, dataset: str, filename: str
) -> None:
    response = client.post(
        f"/api/datasets/{dataset}/files", files=[("files", (filename, b"x", "text/plain"))]
    )
    assert response.status_code == 422
    assert list(datasets_dir.iterdir()) == []


def test_strips_client_paths(client: TestClient, datasets_dir: Path) -> None:
    files = [("files", ("../../notes.txt", b"hi", "text/plain"))]
    assert client.post("/api/datasets/d1/files", files=files).status_code == 201
    assert (datasets_dir / "d1" / "notes.txt").read_bytes() == b"hi"


def test_rejects_invalid_building_json(client: TestClient, datasets_dir: Path) -> None:
    (datasets_dir / "d1").mkdir()
    shutil.copy2(SAMPLE, datasets_dir / "d1" / "building.json")
    bad = json.dumps({"building": {"id": "x"}}).encode()

    response = client.post(
        "/api/datasets/d1/files", files=[("files", ("building.json", bad, "application/json"))]
    )

    assert response.status_code == 422
    assert (datasets_dir / "d1" / "building.json").read_bytes() == SAMPLE.read_bytes()


def test_rejects_oversized_file(client: TestClient) -> None:
    big = b"0" * 1_000_001
    response = client.post("/api/datasets/d1/files", files=[("files", ("a.csv", big, "text/csv"))])
    assert response.status_code == 413

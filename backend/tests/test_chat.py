from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from app.config import Settings
from app.main import create_app


def test_chat_offline_mode_echoes(client: TestClient) -> None:
    response = client.post("/api/chat", json={"message": "Where is the lobby?"})

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "(offline mode) You said: Where is the lobby?"
    assert body["thread_id"]


def test_chat_uses_model_and_keeps_thread(
    settings: Settings, fake_model: GenericFakeChatModel
) -> None:
    with TestClient(create_app(settings, model=fake_model)) as client:
        first = client.post("/api/chat", json={"message": "Where is the 3D printer?"}).json()
        second = client.post(
            "/api/chat", json={"message": "Thanks", "thread_id": first["thread_id"]}
        ).json()

    assert first["reply"] == "The 3D printer is in room 2.04."
    assert second["thread_id"] == first["thread_id"]


def test_chat_rejects_empty_message(client: TestClient) -> None:
    response = client.post("/api/chat", json={"message": ""})

    assert response.status_code == 422

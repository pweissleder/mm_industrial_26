from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    return Settings(
        state_dir=tmp_path_factory.mktemp("state"),
        # No people by default: their start positions would load buildings at startup.
        profiles_file=tmp_path_factory.mktemp("people") / "profiles.json",
        _env_file=None,
        anthropic_api_key=None,
        managed_agent_id=None,
        managed_agent_environment_id=None,
        langsmith_tracing=False,
        langsmith_api_key=None,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def fake_model() -> GenericFakeChatModel:
    return GenericFakeChatModel(
        messages=iter([AIMessage(content="The 3D printer is in room 2.04."), AIMessage("Second.")])
    )

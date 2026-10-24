from fastapi.testclient import TestClient

from app.config import Settings


def test_health_reports_ok(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "environment": "development",
        "llm_enabled": False,
        "managed_agent_enabled": False,
        "tracing_enabled": False,
    }


def test_cors_allows_frontend_origin(client: TestClient) -> None:
    response = client.options(
        "/api/health",
        headers={"Origin": "http://localhost:4321", "Access-Control-Request-Method": "GET"},
    )

    assert response.headers["access-control-allow-origin"] == "http://localhost:4321"


def test_blank_env_values_count_as_unset() -> None:
    settings = Settings(_env_file=None, anthropic_api_key="", managed_agent_id="")

    assert settings.anthropic_api_key is None
    assert settings.managed_agent_id is None

from fastapi.testclient import TestClient

from quayt_service.app import create_app
from quayt_service.settings import Settings


def test_health_and_api_boundary() -> None:
    app = create_app(Settings.from_env({"QUAYT_ENV": "test"}))

    with TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "live"}
        assert client.get("/health/ready").json() == {"status": "ready"}
        response = client.get("/api/v1")

    assert response.status_code == 200
    assert response.json() == {
        "api_version": "v1",
        "service": "quayt-service",
        "service_version": "0.1.0",
    }
    assert response.headers["X-Request-ID"]


def test_http_errors_use_stable_envelope() -> None:
    app = create_app(Settings.from_env({"QUAYT_ENV": "test"}))

    with TestClient(app) as client:
        response = client.get("/api/v1/not-a-route")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Resource not found",
            "request_id": response.headers["X-Request-ID"],
        }
    }


def test_openapi_is_scoped_to_v1_and_health_is_excluded() -> None:
    app = create_app(Settings.from_env({"QUAYT_ENV": "test"}))

    with TestClient(app) as client:
        schema = client.get("/api/v1/openapi.json").json()

    assert set(schema["paths"]) == {"/api/v1"}

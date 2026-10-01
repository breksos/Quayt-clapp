from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Annotated
from uuid import UUID, uuid4

from conftest import TENANT_ID, FakeRepository, FakeVerifier, FakeVesselCallRepository
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from quayt_service.app import create_app
from quayt_service.auth import require_request_context
from quayt_service.context import RequestContext
from quayt_service.settings import Settings


def client_for(
    settings: Settings, repository: FakeRepository, verifier: FakeVerifier
) -> TestClient:
    return TestClient(
        create_app(
            settings,
            repository=repository,
            oidc_verifier=verifier,
            vessel_call_repository=FakeVesselCallRepository(),
        )
    )


def create_device_session(
    client: TestClient,
    *,
    client_instance_id: UUID | None = None,
    access_token: str = "access-one",
) -> tuple[dict[str, object], str]:
    response = client.post(
        "/api/v1/session",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"client_instance_id": str(client_instance_id or uuid4())},
    )
    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"
    payload = response.json()
    return payload["session"], payload["session_credential"]


def test_health_config_and_api_boundary(
    settings: Settings, fake_repository: FakeRepository, fake_verifier: FakeVerifier
) -> None:
    with client_for(settings, fake_repository, fake_verifier) as client:
        assert client.get("/health/live").json() == {"status": "live"}
        assert client.get("/health/ready").json() == {"status": "ready"}
        config = client.get("/api/v1/auth/config").json()
        boundary = client.get("/api/v1").json()

    assert config == {
        "issuer": "http://127.0.0.1:9000",
        "authorization_endpoint": "http://127.0.0.1:9000/authorize",
        "token_endpoint": "http://127.0.0.1:9000/token",
        "client_id": "quayt-native",
        "scopes": ["openid", "profile"],
        "pkce_methods": ["S256"],
        "loopback_host": "127.0.0.1",
    }
    assert boundary["api_version"] == "v1"


def test_readiness_fails_when_a_dependency_is_unavailable(settings: Settings) -> None:
    with client_for(settings, FakeRepository(ready=False), FakeVerifier()) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_session_selection_refresh_replay_and_logout(
    settings: Settings, fake_repository: FakeRepository, fake_verifier: FakeVerifier
) -> None:
    with client_for(settings, fake_repository, fake_verifier) as client:
        session, first_credential = create_device_session(client)
        assert session["selected_tenant_id"] is None
        headers = {"Authorization": f"Session {first_credential}"}
        assert client.get("/api/v1/session", headers=headers).status_code == 200

        selected = client.post(
            "/api/v1/session/tenant",
            headers=headers,
            json={"tenant_id": str(TENANT_ID), "expected_session_version": 1},
        )
        assert selected.status_code == 200
        assert selected.json()["selected_tenant_id"] == str(TENANT_ID)
        assert "session_credential" not in selected.json()

        refreshed = client.post(
            "/api/v1/session/refresh",
            headers={
                "Authorization": "Bearer access-two",
                "X-Quayt-Session-Credential": first_credential,
            },
        )
        assert refreshed.status_code == 200
        second_credential = refreshed.json()["session_credential"]
        assert second_credential != first_credential
        assert client.get("/api/v1/session", headers=headers).status_code == 401

        replay = client.post(
            "/api/v1/session/refresh",
            headers={
                "Authorization": "Bearer access-three",
                "X-Quayt-Session-Credential": first_credential,
            },
        )
        assert replay.status_code == 401
        second_headers = {"Authorization": f"Session {second_credential}"}
        assert client.get("/api/v1/session", headers=second_headers).status_code == 401

        assert client.post("/api/v1/session/logout", headers=second_headers).status_code == 204
        assert client.post("/api/v1/session/logout", headers=second_headers).status_code == 204


def test_cross_tenant_selection_is_generic_denial(
    settings: Settings, fake_repository: FakeRepository, fake_verifier: FakeVerifier
) -> None:
    with client_for(settings, fake_repository, fake_verifier) as client:
        _, credential = create_device_session(client)
        response = client.post(
            "/api/v1/session/tenant",
            headers={"Authorization": f"Session {credential}"},
            json={
                "tenant_id": str(UUID("30000000-0000-0000-0000-000000000001")),
                "expected_session_version": 1,
            },
        )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "tenant_access_denied"


def test_request_context_rechecks_membership_for_every_request(
    settings: Settings, fake_repository: FakeRepository, fake_verifier: FakeVerifier
) -> None:
    app: FastAPI = create_app(
        settings,
        repository=fake_repository,
        oidc_verifier=fake_verifier,
        vessel_call_repository=FakeVesselCallRepository(),
    )

    @app.get("/test/context")
    def context_probe(
        context: Annotated[RequestContext, Depends(require_request_context)],
    ) -> dict[str, str]:
        return {"tenant_id": str(context.tenant.tenant_id)}

    with TestClient(app) as client:
        _, credential = create_device_session(client)
        headers = {"Authorization": f"Session {credential}"}
        selected = client.post(
            "/api/v1/session/tenant",
            headers=headers,
            json={"tenant_id": str(TENANT_ID), "expected_session_version": 1},
        )
        assert selected.status_code == 200
        assert client.get("/test/context", headers=headers).status_code == 200

        session_id = next(iter(fake_repository.views))
        fake_repository.views[session_id] = replace(
            fake_repository.views[session_id], memberships=()
        )
        denied = client.get("/test/context", headers=headers)

    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "tenant_access_denied"


def test_session_creation_retry_recovers_same_credential(
    settings: Settings, fake_repository: FakeRepository, fake_verifier: FakeVerifier
) -> None:
    client_instance_id = uuid4()
    with client_for(settings, fake_repository, fake_verifier) as client:
        first_session, first_credential = create_device_session(
            client, client_instance_id=client_instance_id
        )
        retried_session, retried_credential = create_device_session(
            client, client_instance_id=client_instance_id
        )
    assert retried_session == first_session
    assert retried_credential == first_credential


def test_provider_token_reuse_with_different_client_is_denied(
    settings: Settings, fake_repository: FakeRepository, fake_verifier: FakeVerifier
) -> None:
    with client_for(settings, fake_repository, fake_verifier) as client:
        create_device_session(client, client_instance_id=uuid4())
        response = client.post(
            "/api/v1/session",
            headers={"Authorization": "Bearer access-one"},
            json={"client_instance_id": str(uuid4())},
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_failed"


def test_concurrent_provider_token_creation_has_one_lineage(
    settings: Settings, fake_repository: FakeRepository, fake_verifier: FakeVerifier
) -> None:
    app = create_app(
        settings,
        repository=fake_repository,
        oidc_verifier=fake_verifier,
        vessel_call_repository=FakeVesselCallRepository(),
    )

    def attempt(client_instance_id: UUID) -> tuple[int, dict[str, object]]:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/session",
                headers={"Authorization": "Bearer access-one"},
                json={"client_instance_id": str(client_instance_id)},
            )
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, (uuid4(), uuid4())))

    assert sorted(status for status, _ in results) == [201, 401]
    assert len(fake_repository.provider_tokens) == 1


def test_refresh_rejects_provider_token_already_bound_to_a_lineage(
    settings: Settings, fake_repository: FakeRepository, fake_verifier: FakeVerifier
) -> None:
    with client_for(settings, fake_repository, fake_verifier) as client:
        _, credential = create_device_session(client)
        response = client.post(
            "/api/v1/session/refresh",
            headers={
                "Authorization": "Bearer access-one",
                "X-Quayt-Session-Credential": credential,
            },
        )
        original = client.get("/api/v1/session", headers={"Authorization": f"Session {credential}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_failed"
    assert original.status_code == 200


def test_active_session_cap_is_generic_denial(
    settings: Settings, fake_repository: FakeRepository, fake_verifier: FakeVerifier
) -> None:
    limited = replace(settings, max_active_sessions_per_actor=1)
    with client_for(limited, fake_repository, fake_verifier) as client:
        create_device_session(client, client_instance_id=uuid4(), access_token="access-one")
        response = client.post(
            "/api/v1/session",
            headers={"Authorization": "Bearer access-two"},
            json={"client_instance_id": str(uuid4())},
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_failed"


def test_invalid_oidc_token_uses_safe_error(
    settings: Settings, fake_repository: FakeRepository, fake_verifier: FakeVerifier
) -> None:
    with client_for(settings, fake_repository, fake_verifier) as client:
        response = client.post(
            "/api/v1/session",
            headers={"Authorization": "Bearer invalid"},
            json={"client_instance_id": str(uuid4())},
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_failed"
    assert "invalid" not in response.text


def test_missing_oidc_token_uses_safe_error(
    settings: Settings, fake_repository: FakeRepository, fake_verifier: FakeVerifier
) -> None:
    with client_for(settings, fake_repository, fake_verifier) as client:
        response = client.post(
            "/api/v1/session",
            json={"client_instance_id": str(uuid4())},
        )
    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "authentication_failed",
            "message": "Authentication failed",
            "request_id": response.headers["X-Request-ID"],
        }
    }


def test_http_errors_use_stable_envelope(
    settings: Settings, fake_repository: FakeRepository, fake_verifier: FakeVerifier
) -> None:
    with client_for(settings, fake_repository, fake_verifier) as client:
        response = client.get("/api/v1/not-a-route")
    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "not_found",
        "message": "Resource not found",
        "request_id": response.headers["X-Request-ID"],
    }

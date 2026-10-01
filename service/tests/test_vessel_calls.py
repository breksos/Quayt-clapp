from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID, uuid4

from conftest import ACTOR_ID, TENANT_ID, FakeRepository, FakeVerifier, FakeVesselCallRepository
from fastapi.testclient import TestClient

from quayt_service.app import create_app
from quayt_service.auth import require_request_context
from quayt_service.context import ActorContext, RequestContext, TenantContext
from quayt_service.domain import MembershipView
from quayt_service.seed_vessel_calls import seed_vessel_calls
from quayt_service.settings import Environment, SettingsError
from quayt_service.vessel_calls import VesselCallResponse, VesselCallStatus

FOREIGN_TENANT_ID = UUID("20000000-0000-0000-0000-000000000002")
OWN_CALL_ID = UUID("30000000-0000-0000-0000-000000000001")
FOREIGN_CALL_ID = UUID("30000000-0000-0000-0000-000000000002")


def item(
    identifier: UUID, tenant_id: UUID, name: str, status: VesselCallStatus
) -> VesselCallResponse:
    return VesselCallResponse(
        id=identifier,
        tenant_id=tenant_id,
        vessel_name=name,
        imo_number="9000001",
        agent_name="Demo Agency",
        berth="A1",
        status=status,
        eta=datetime(2030, 1, 10, tzinfo=timezone.utc),
        etd=None,
    )


def test_api_isolates_tenants_filters_and_hides_foreign_ids(settings) -> None:
    vessels = FakeVesselCallRepository(
        [
            item(OWN_CALL_ID, TENANT_ID, "Northstar", VesselCallStatus.EXPECTED),
            item(FOREIGN_CALL_ID, FOREIGN_TENANT_ID, "Foreign", VesselCallStatus.ARRIVED),
        ]
    )
    app = create_app(
        settings,
        repository=FakeRepository(),
        oidc_verifier=FakeVerifier(),
        vessel_call_repository=vessels,
    )
    app.dependency_overrides[require_request_context] = lambda: RequestContext(
        tenant=TenantContext(tenant_id=TENANT_ID),
        actor=ActorContext(actor_id=ACTOR_ID, subject="subject", scopes=("operator",)),
    )
    with TestClient(app) as client:
        listing = client.get("/api/v1/vessel-calls?status=expected&query=north")
        own = client.get(f"/api/v1/vessel-calls/{OWN_CALL_ID}")
        foreign = client.get(f"/api/v1/vessel-calls/{FOREIGN_CALL_ID}")
        summary = client.get("/api/v1/vessel-calls/summary")
    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()["items"]] == [str(OWN_CALL_ID)]
    assert own.status_code == 200
    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "not_found"
    assert summary.json()["total"] == 1
    for response in (listing, own, foreign, summary):
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Pragma"] == "no-cache"


def test_filter_bounds_and_time_validation(settings) -> None:
    app = create_app(
        settings,
        repository=FakeRepository(),
        oidc_verifier=FakeVerifier(),
        vessel_call_repository=FakeVesselCallRepository(),
    )
    app.dependency_overrides[require_request_context] = lambda: RequestContext(
        tenant=TenantContext(tenant_id=TENANT_ID),
        actor=ActorContext(actor_id=ACTOR_ID, subject="subject", scopes=()),
    )
    with TestClient(app) as client:
        assert client.get("/api/v1/vessel-calls?limit=101").status_code == 422
        assert client.get("/api/v1/vessel-calls?offset=10001").status_code == 422
        assert client.get("/api/v1/vessel-calls?query=%20%20").status_code == 422
        assert client.get("/api/v1/vessel-calls?eta_from=2030-01-01T00:00:00").status_code == 422
        assert (
            client.get(
                "/api/v1/vessel-calls?eta_from=2030-02-01T00:00:00Z&eta_to=2030-01-01T00:00:00Z"
            ).status_code
            == 422
        )


def test_seed_refuses_non_development_without_opening_database(settings) -> None:
    assert settings.environment is Environment.TEST
    try:
        seed_vessel_calls(settings, TENANT_ID, None)  # type: ignore[arg-type]
    except SettingsError as exc:
        assert "only in development" in str(exc)
    else:
        raise AssertionError("seed did not fail closed")


def _create_selected_session(client: TestClient) -> tuple[dict[str, str], UUID]:
    created = client.post(
        "/api/v1/session",
        headers={"Authorization": "Bearer access-one"},
        json={"client_instance_id": str(uuid4())},
    )
    assert created.status_code == 201
    credential = created.json()["session_credential"]
    selected = client.post(
        "/api/v1/session/tenant",
        headers={"Authorization": f"Session {credential}"},
        json={"tenant_id": str(TENANT_ID), "expected_session_version": 1},
    )
    assert selected.status_code == 200
    return {"Authorization": f"Session {credential}"}, UUID(created.json()["session"]["session_id"])


def test_vessel_call_http_authentication_denials_use_real_session_dependency(settings) -> None:
    repository = FakeRepository()
    app = create_app(
        settings,
        repository=repository,
        oidc_verifier=FakeVerifier(),
        vessel_call_repository=FakeVesselCallRepository(),
    )
    with TestClient(app) as client:
        missing = client.get("/api/v1/vessel-calls")
        invalid = client.get("/api/v1/vessel-calls", headers={"Authorization": "Session invalid"})
        headers, session_id = _create_selected_session(client)
        for key, (owner, _) in tuple(repository.credentials.items()):
            if owner == session_id:
                repository.credentials[key] = (owner, "revoked")
        revoked = client.get("/api/v1/vessel-calls", headers=headers)

    assert missing.status_code == invalid.status_code == revoked.status_code == 401
    for response in (missing, invalid, revoked):
        assert response.json()["error"]["code"] == "authentication_failed"
        assert response.headers["Cache-Control"] == "no-store"


def test_vessel_call_http_rechecks_membership_and_selected_tenant(settings) -> None:
    repository = FakeRepository()
    vessels = FakeVesselCallRepository(
        [
            item(OWN_CALL_ID, TENANT_ID, "Northstar", VesselCallStatus.EXPECTED),
            item(FOREIGN_CALL_ID, FOREIGN_TENANT_ID, "Foreign", VesselCallStatus.ARRIVED),
        ]
    )
    app = create_app(
        settings,
        repository=repository,
        oidc_verifier=FakeVerifier(),
        vessel_call_repository=vessels,
    )
    with TestClient(app) as client:
        headers, session_id = _create_selected_session(client)
        original = repository.views[session_id]
        repository.views[session_id] = replace(original, memberships=())
        removed = client.get("/api/v1/vessel-calls", headers=headers)

        foreign_membership = MembershipView(FOREIGN_TENANT_ID, "Terminal B", ("operator",))
        repository.views[session_id] = replace(
            original,
            memberships=(*original.memberships, foreign_membership),
            selected_tenant_id=FOREIGN_TENANT_ID,
        )
        old_detail = client.get(f"/api/v1/vessel-calls/{OWN_CALL_ID}", headers=headers)
        switched_list = client.get("/api/v1/vessel-calls", headers=headers)

    assert removed.status_code == 403
    assert removed.json()["error"]["code"] == "tenant_access_denied"
    assert old_detail.status_code == 404
    assert [row["id"] for row in switched_list.json()["items"]] == [str(FOREIGN_CALL_ID)]

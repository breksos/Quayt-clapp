from __future__ import annotations

import base64
from dataclasses import replace
from datetime import datetime
from threading import Lock
from uuid import UUID

import pytest

from quayt_service.domain import MembershipView, SessionView, VerifiedIdentity
from quayt_service.oidc import TokenVerificationError
from quayt_service.settings import Settings

ACTOR_ID = UUID("10000000-0000-0000-0000-000000000001")
TENANT_ID = UUID("20000000-0000-0000-0000-000000000001")


def settings_env(**overrides: str) -> dict[str, str]:
    values = {
        "QUAYT_ENV": "test",
        "QUAYT_DATABASE_URL": "postgresql+psycopg://quayt:test@localhost/quayt_test",
        "QUAYT_AUTH_ISSUER": "http://127.0.0.1:9000",
        "QUAYT_AUTH_AUDIENCE": "quayt-service",
        "QUAYT_AUTH_JWKS_URL": "http://127.0.0.1:9000/jwks",
        "QUAYT_AUTHORIZATION_ENDPOINT": "http://127.0.0.1:9000/authorize",
        "QUAYT_TOKEN_ENDPOINT": "http://127.0.0.1:9000/token",
        "QUAYT_OIDC_CLIENT_ID": "quayt-native",
        "QUAYT_OIDC_SCOPES": "openid profile",
        "QUAYT_SESSION_HASH_KEY": base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("="),
    }
    values.update(overrides)
    return values


@pytest.fixture
def settings() -> Settings:
    return Settings.from_env(settings_env())


class FakeVerifier:
    def __init__(self, *, ready: bool = True) -> None:
        self.ready_value = ready

    def verify(self, access_token: str) -> VerifiedIdentity:
        if access_token not in {"access-one", "access-two", "access-three"}:
            raise TokenVerificationError("rejected")
        return VerifiedIdentity("http://127.0.0.1:9000", "subject-1", "Operator")

    def ready(self) -> bool:
        return self.ready_value


class FakeRepository:
    def __init__(self, *, ready: bool = True) -> None:
        self.ready_value = ready
        self.views: dict[UUID, SessionView] = {}
        self.credentials: dict[bytes, tuple[UUID, str]] = {}
        self.provider_tokens: dict[bytes, UUID] = {}
        self.client_instances: dict[UUID, UUID] = {}
        self._lock = Lock()

    def ready(self) -> bool:
        return self.ready_value

    def create_session(
        self,
        identity: VerifiedIdentity,
        client_instance_id: UUID,
        session_id: UUID,
        credential_hash: bytes,
        token_digest: bytes,
        expires_at: datetime,
        request_id: str,
        max_active_sessions: int,
    ) -> SessionView:
        del identity, request_id
        with self._lock:
            existing_id = self.provider_tokens.get(token_digest)
            if existing_id is not None:
                if self.client_instances[existing_id] == client_instance_id and any(
                    owner == existing_id and status == "current"
                    for owner, status in self.credentials.values()
                ):
                    return self.views[existing_id]
                raise PermissionError("provider token already consumed")
            replaced = [
                owner
                for owner, instance_id in self.client_instances.items()
                if instance_id == client_instance_id
                and any(
                    credential_owner == owner and status == "current"
                    for credential_owner, status in self.credentials.values()
                )
            ]
            for owner in replaced:
                for key, (credential_owner, _) in tuple(self.credentials.items()):
                    if credential_owner == owner:
                        self.credentials[key] = (owner, "revoked")
            active_sessions = {
                owner for owner, status in self.credentials.values() if status == "current"
            }
            if len(active_sessions) >= max_active_sessions:
                raise PermissionError("active session limit reached")
            view = SessionView(
                session_id=session_id,
                version=1,
                actor_id=ACTOR_ID,
                subject="subject-1",
                display_name="Operator",
                memberships=(MembershipView(TENANT_ID, "Terminal A", ("operator",)),),
                selected_tenant_id=None,
                expires_at=expires_at,
            )
            self.views[session_id] = view
            self.credentials[credential_hash] = (session_id, "current")
            self.provider_tokens[token_digest] = session_id
            self.client_instances[session_id] = client_instance_id
            return view

    def get_session(self, session_id: UUID, credential_hash: bytes) -> SessionView | None:
        record = self.credentials.get(credential_hash)
        if record != (session_id, "current"):
            return None
        return self.views.get(session_id)

    def select_tenant(
        self,
        session_id: UUID,
        credential_hash: bytes,
        tenant_id: UUID,
        expected_version: int,
        request_id: str,
    ) -> tuple[str, SessionView | None]:
        del request_id
        view = self.get_session(session_id, credential_hash)
        if view is None:
            return "invalid", None
        if view.version != expected_version:
            return "conflict", view
        if tenant_id != TENANT_ID:
            return "denied", None
        selected = replace(view, version=view.version + 1, selected_tenant_id=tenant_id)
        self.views[session_id] = selected
        return "ok", selected

    def refresh(
        self,
        identity: VerifiedIdentity,
        session_id: UUID,
        old_credential_hash: bytes,
        new_credential_hash: bytes,
        token_digest: bytes,
        expires_at: datetime,
        request_id: str,
    ) -> tuple[str, SessionView | None]:
        del identity, request_id
        with self._lock:
            if token_digest in self.provider_tokens:
                return "invalid", None
            old = self.credentials.get(old_credential_hash)
            if old is None or old[0] != session_id:
                return "invalid", None
            if old[1] != "current":
                for key, (owner, _) in tuple(self.credentials.items()):
                    if owner == session_id:
                        self.credentials[key] = (owner, "revoked")
                return "replay", None
            self.credentials[old_credential_hash] = (session_id, "rotated")
            self.credentials[new_credential_hash] = (session_id, "current")
            self.provider_tokens[token_digest] = session_id
            view = replace(
                self.views[session_id],
                version=self.views[session_id].version + 1,
                expires_at=expires_at,
            )
            self.views[session_id] = view
            return "ok", view

    def logout(self, session_id: UUID, credential_hash: bytes, request_id: str) -> None:
        del request_id
        record = self.credentials.get(credential_hash)
        if record is None or record[0] != session_id:
            return
        for key, (owner, _) in tuple(self.credentials.items()):
            if owner == session_id:
                self.credentials[key] = (owner, "revoked")


@pytest.fixture
def fake_repository() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def fake_verifier() -> FakeVerifier:
    return FakeVerifier()

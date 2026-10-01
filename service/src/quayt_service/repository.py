"""Transactional PostgreSQL repository for authentication sessions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from quayt_service.domain import MembershipView, SessionView, VerifiedIdentity
from quayt_service.models import (
    Actor,
    AuthAudit,
    CredentialGeneration,
    DeviceSession,
    Membership,
    Tenant,
)

MIGRATION_HEAD = "0002_vessel_calls"


class SessionRepository(Protocol):
    def ready(self) -> bool: ...

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
    ) -> SessionView: ...

    def get_session(self, session_id: UUID, credential_hash: bytes) -> SessionView | None: ...

    def select_tenant(
        self,
        session_id: UUID,
        credential_hash: bytes,
        tenant_id: UUID,
        expected_version: int,
        request_id: str,
    ) -> tuple[str, SessionView | None]: ...

    def refresh(
        self,
        identity: VerifiedIdentity,
        session_id: UUID,
        old_credential_hash: bytes,
        new_credential_hash: bytes,
        token_digest: bytes,
        expires_at: datetime,
        request_id: str,
    ) -> tuple[str, SessionView | None]: ...

    def logout(self, session_id: UUID, credential_hash: bytes, request_id: str) -> None: ...


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PostgresSessionRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def ready(self) -> bool:
        try:
            with self._sessions() as db:
                db.execute(text("SELECT 1"))
                revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                return str(revision) == MIGRATION_HEAD
        except Exception:
            return False

    def _lock_token_digest(self, db: Session, token_digest: bytes) -> None:
        lock_key = int.from_bytes(token_digest[:8], byteorder="big", signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})

    def _audit(
        self,
        db: Session,
        event_type: str,
        request_id: str,
        actor_id: UUID | None = None,
        session_id: UUID | None = None,
    ) -> None:
        db.add(
            AuthAudit(
                actor_id=actor_id,
                session_id=session_id,
                event_type=event_type,
                occurred_at=utc_now(),
                request_id=request_id,
                details={},
            )
        )

    def _view(self, db: Session, session: DeviceSession) -> SessionView:
        actor = db.get(Actor, session.actor_id)
        if actor is None:
            raise RuntimeError("session actor is missing")
        rows = db.execute(
            select(Membership, Tenant)
            .join(Tenant, Tenant.id == Membership.tenant_id)
            .where(
                Membership.actor_id == actor.id,
                Membership.status == "active",
                Tenant.status == "active",
            )
            .order_by(Tenant.name, Tenant.id)
        ).all()
        memberships = tuple(
            MembershipView(tenant.id, tenant.name, tuple(membership.roles))
            for membership, tenant in rows
        )
        active_ids = {item.tenant_id for item in memberships}
        selected = session.selected_tenant_id if session.selected_tenant_id in active_ids else None
        return SessionView(
            session_id=session.id,
            version=session.version,
            actor_id=actor.id,
            subject=actor.subject,
            display_name=actor.display_name,
            memberships=memberships,
            selected_tenant_id=selected,
            expires_at=session.expires_at,
        )

    def _current_session(
        self,
        db: Session,
        session_id: UUID,
        credential_hash: bytes,
        *,
        lock: bool = False,
    ) -> DeviceSession | None:
        statement = (
            select(DeviceSession)
            .join(CredentialGeneration, CredentialGeneration.session_id == DeviceSession.id)
            .join(Actor, Actor.id == DeviceSession.actor_id)
            .where(
                DeviceSession.id == session_id,
                DeviceSession.revoked_at.is_(None),
                DeviceSession.expires_at > utc_now(),
                CredentialGeneration.secret_hash == credential_hash,
                CredentialGeneration.status == "current",
                CredentialGeneration.expires_at > utc_now(),
                Actor.status == "active",
            )
        )
        if lock:
            statement = statement.with_for_update(of=DeviceSession)
        return db.execute(statement).scalar_one_or_none()

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
        now = utc_now()
        with self._sessions.begin() as db:
            self._lock_token_digest(db, token_digest)
            existing = db.execute(
                select(CredentialGeneration, DeviceSession, Actor)
                .join(DeviceSession, DeviceSession.id == CredentialGeneration.session_id)
                .join(Actor, Actor.id == DeviceSession.actor_id)
                .where(CredentialGeneration.provider_token_digest == token_digest)
            ).one_or_none()
            if existing is not None:
                existing_credential, existing_session, existing_actor = existing
                if (
                    existing_actor.issuer == identity.issuer
                    and existing_actor.subject == identity.subject
                    and existing_actor.status == "active"
                    and existing_session.client_instance_id == client_instance_id
                    and existing_session.revoked_at is None
                    and existing_session.expires_at > now
                    and existing_credential.status == "current"
                    and existing_credential.expires_at > now
                ):
                    return self._view(db, existing_session)
                raise PermissionError("provider token already consumed")
            actor_id = db.execute(
                insert(Actor)
                .values(
                    id=uuid4(),
                    issuer=identity.issuer,
                    subject=identity.subject,
                    display_name=identity.display_name,
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_update(
                    constraint="uq_actors_issuer_subject",
                    set_={"display_name": identity.display_name, "updated_at": now},
                )
                .returning(Actor.id)
            ).scalar_one()
            actor = db.get(Actor, actor_id)
            if actor is None or actor.status != "active":
                raise PermissionError("actor is not active")
            actor = db.execute(
                select(Actor).where(Actor.id == actor_id).with_for_update()
            ).scalar_one()
            old_sessions = db.scalars(
                select(DeviceSession).where(
                    DeviceSession.actor_id == actor_id,
                    DeviceSession.client_instance_id == client_instance_id,
                    DeviceSession.revoked_at.is_(None),
                )
            ).all()
            for old in old_sessions:
                old.revoked_at = now
                old.revoke_reason = "replaced"
                db.execute(
                    update(CredentialGeneration)
                    .where(CredentialGeneration.session_id == old.id)
                    .values(status="revoked")
                )
            active_count = db.execute(
                select(func.count(DeviceSession.id)).where(
                    DeviceSession.actor_id == actor_id,
                    DeviceSession.revoked_at.is_(None),
                    DeviceSession.expires_at > now,
                )
            ).scalar_one()
            if active_count >= max_active_sessions:
                raise PermissionError("active session limit reached")
            device_session = DeviceSession(
                id=session_id,
                actor_id=actor_id,
                client_instance_id=client_instance_id,
                oidc_token_digest=token_digest,
                version=1,
                created_at=now,
                expires_at=expires_at,
            )
            db.add(device_session)
            db.add(
                CredentialGeneration(
                    id=uuid4(),
                    session_id=session_id,
                    generation=1,
                    secret_hash=credential_hash,
                    provider_token_digest=token_digest,
                    status="current",
                    issued_at=now,
                    expires_at=expires_at,
                )
            )
            self._audit(db, "session_created", request_id, actor_id, session_id)
            db.flush()
            return self._view(db, device_session)

    def get_session(self, session_id: UUID, credential_hash: bytes) -> SessionView | None:
        with self._sessions() as db:
            session = self._current_session(db, session_id, credential_hash)
            if session is None:
                return None
            actor = db.get(Actor, session.actor_id)
            if actor is None or actor.status != "active":
                return None
            return self._view(db, session)

    def select_tenant(
        self,
        session_id: UUID,
        credential_hash: bytes,
        tenant_id: UUID,
        expected_version: int,
        request_id: str,
    ) -> tuple[str, SessionView | None]:
        with self._sessions.begin() as db:
            session = self._current_session(db, session_id, credential_hash, lock=True)
            if session is None:
                return "invalid", None
            if session.version != expected_version:
                return "conflict", self._view(db, session)
            membership = db.execute(
                select(Membership)
                .join(Tenant, Tenant.id == Membership.tenant_id)
                .where(
                    Membership.actor_id == session.actor_id,
                    Membership.tenant_id == tenant_id,
                    Membership.status == "active",
                    Tenant.status == "active",
                )
            ).scalar_one_or_none()
            if membership is None:
                return "denied", None
            session.selected_tenant_id = tenant_id
            session.version += 1
            self._audit(db, "tenant_selected", request_id, session.actor_id, session.id)
            db.flush()
            return "ok", self._view(db, session)

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
        now = utc_now()
        with self._sessions.begin() as db:
            self._lock_token_digest(db, token_digest)
            if (
                db.execute(
                    select(CredentialGeneration.id).where(
                        CredentialGeneration.provider_token_digest == token_digest
                    )
                ).scalar_one_or_none()
                is not None
            ):
                return "invalid", None
            session = db.execute(
                select(DeviceSession).where(DeviceSession.id == session_id).with_for_update()
            ).scalar_one_or_none()
            credential = db.execute(
                select(CredentialGeneration).where(
                    CredentialGeneration.session_id == session_id,
                    CredentialGeneration.secret_hash == old_credential_hash,
                )
            ).scalar_one_or_none()
            if session is None or credential is None:
                return "invalid", None
            actor = db.get(Actor, session.actor_id)
            if (
                actor is None
                or actor.status != "active"
                or actor.issuer != identity.issuer
                or actor.subject != identity.subject
            ):
                return "invalid", None
            if session.revoked_at is not None or session.expires_at <= now:
                return "invalid", None
            if credential.status == "rotated":
                session.revoked_at = now
                session.revoke_reason = "credential_replay"
                db.execute(
                    update(CredentialGeneration)
                    .where(CredentialGeneration.session_id == session.id)
                    .values(status="revoked")
                )
                self._audit(db, "credential_replay", request_id, actor.id, session.id)
                return "replay", None
            if credential.status != "current" or credential.expires_at <= now:
                return "invalid", None
            credential.status = "rotated"
            credential.consumed_at = now
            next_generation = credential.generation + 1
            db.add(
                CredentialGeneration(
                    id=uuid4(),
                    session_id=session.id,
                    generation=next_generation,
                    secret_hash=new_credential_hash,
                    provider_token_digest=token_digest,
                    status="current",
                    issued_at=now,
                    expires_at=expires_at,
                )
            )
            session.oidc_token_digest = token_digest
            session.expires_at = expires_at
            session.version += 1
            self._audit(db, "credential_rotated", request_id, actor.id, session.id)
            db.flush()
            return "ok", self._view(db, session)

    def logout(self, session_id: UUID, credential_hash: bytes, request_id: str) -> None:
        now = utc_now()
        with self._sessions.begin() as db:
            credential = db.execute(
                select(CredentialGeneration).where(
                    CredentialGeneration.session_id == session_id,
                    CredentialGeneration.secret_hash == credential_hash,
                )
            ).scalar_one_or_none()
            if credential is None:
                return
            session = db.execute(
                select(DeviceSession).where(DeviceSession.id == session_id).with_for_update()
            ).scalar_one_or_none()
            if session is None or session.revoked_at is not None:
                return
            session.revoked_at = now
            session.revoke_reason = "logout"
            db.execute(
                update(CredentialGeneration)
                .where(CredentialGeneration.session_id == session.id)
                .values(status="revoked")
            )
            self._audit(db, "session_logged_out", request_id, session.actor_id, session.id)


def session_expiry(ttl_seconds: int) -> datetime:
    return utc_now() + timedelta(seconds=ttl_seconds)

"""Phase 2 identity, tenant, membership, and device sessions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_auth_sessions"
down_revision = None
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "actors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("issuer", sa.String(512), nullable=False),
        sa.Column("subject", sa.String(512), nullable=False),
        sa.Column("display_name", sa.String(255)),
        sa.Column("status", sa.String(20), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("status IN ('active','disabled')", name="ck_actors_status"),
        sa.UniqueConstraint("issuer", "subject", name="uq_actors_issuer_subject"),
    )
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("status IN ('active','disabled')", name="ck_tenants_status"),
    )
    op.create_table(
        "memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "actor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("actors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("roles", postgresql.JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("status IN ('active','disabled')", name="ck_memberships_status"),
        sa.CheckConstraint("version > 0", name="ck_memberships_version"),
        sa.UniqueConstraint("actor_id", "tenant_id", name="uq_memberships_actor_tenant"),
    )
    op.create_table(
        "device_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "actor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("actors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "selected_tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="SET NULL"),
        ),
        sa.Column("oidc_token_digest", sa.LargeBinary(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoke_reason", sa.String(64)),
        sa.CheckConstraint("version > 0", name="ck_device_sessions_version"),
    )
    op.create_index(
        "ix_device_sessions_actor_client", "device_sessions", ["actor_id", "client_instance_id"]
    )
    op.create_index(
        "uq_device_sessions_active_actor_client",
        "device_sessions",
        ["actor_id", "client_instance_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "credential_generations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("device_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("secret_hash", sa.LargeBinary(32), nullable=False),
        sa.Column("provider_token_digest", sa.LargeBinary(32), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('current','rotated','revoked')", name="ck_credential_status"
        ),
        sa.UniqueConstraint("session_id", "generation", name="uq_credential_session_generation"),
        sa.UniqueConstraint("secret_hash", name="uq_credential_secret_hash"),
        sa.UniqueConstraint("provider_token_digest", name="uq_credential_provider_token_digest"),
    )
    op.create_index(
        "uq_credential_current",
        "credential_generations",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("status = 'current'"),
    )
    op.create_table(
        "auth_audit",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True)),
        sa.Column("session_id", postgresql.UUID(as_uuid=True)),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
    )
    op.execute("""
        CREATE FUNCTION deny_auth_audit_mutation() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'auth_audit is append-only'; END;
        $$ LANGUAGE plpgsql
    """)
    op.execute(
        "CREATE TRIGGER auth_audit_immutable BEFORE UPDATE OR DELETE ON auth_audit "
        "FOR EACH ROW EXECUTE FUNCTION deny_auth_audit_mutation()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS auth_audit_immutable ON auth_audit")
    op.execute("DROP FUNCTION IF EXISTS deny_auth_audit_mutation")
    op.drop_table("auth_audit")
    op.drop_index("uq_credential_current", table_name="credential_generations")
    op.drop_table("credential_generations")
    op.drop_index("uq_device_sessions_active_actor_client", table_name="device_sessions")
    op.drop_index("ix_device_sessions_actor_client", table_name="device_sessions")
    op.drop_table("device_sessions")
    op.drop_table("memberships")
    op.drop_table("tenants")
    op.drop_table("actors")

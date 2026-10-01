"""Authoritative tenant-owned vessel calls.

This unreleased forward revision owns its cluster-wide runtime role. A collision
fails migration rather than trusting or altering a pre-existing role. Run with a
dedicated migration owner that can create/grant roles, using the public schema.
The bootstrap login receives runtime membership; a separate service login must
be granted SET membership in this role by its administrator before service use.
The runtime role owns no objects and receives no membership in privileged roles.
Downgrade revokes only this revision's grants, never DROP OWNED/CASCADE.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_vessel_calls"
down_revision = "0001_auth_sessions"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "quayt_vessel_calls_runtime"


def upgrade() -> None:
    op.create_table(
        "vessel_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("vessel_name", sa.String(255), nullable=False),
        sa.Column("imo_number", sa.String(7), nullable=False),
        sa.Column("agent_name", sa.String(255)),
        sa.Column("berth", sa.String(100)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("eta", sa.DateTime(timezone=True), nullable=False),
        sa.Column("etd", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('expected','arrived','berthed','departed','cancelled')",
            name="ck_vessel_calls_status",
        ),
        sa.CheckConstraint("imo_number ~ '^[0-9]{7}$'", name="ck_vessel_calls_imo_number"),
    )
    op.create_index("ix_vessel_calls_tenant_eta", "vessel_calls", ["tenant_id", "eta", "id"])
    op.create_index(
        "ix_vessel_calls_tenant_status_eta", "vessel_calls", ["tenant_id", "status", "eta"]
    )
    op.execute("ALTER TABLE vessel_calls ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE vessel_calls FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY vessel_calls_tenant_isolation ON vessel_calls
        USING (tenant_id::text = current_setting('quayt.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('quayt.tenant_id', true))
    """)
    op.execute(
        f"CREATE ROLE {RUNTIME_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS "
        "NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT"
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {RUNTIME_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON vessel_calls TO {RUNTIME_ROLE}")
    op.execute(f"GRANT {RUNTIME_ROLE} TO CURRENT_USER")


def downgrade() -> None:
    op.execute(f"REVOKE {RUNTIME_ROLE} FROM CURRENT_USER")
    op.execute(f"REVOKE ALL PRIVILEGES ON vessel_calls FROM {RUNTIME_ROLE}")
    op.execute(f"REVOKE ALL PRIVILEGES ON SCHEMA public FROM {RUNTIME_ROLE}")
    op.execute(f"DROP ROLE {RUNTIME_ROLE}")
    op.drop_index("ix_vessel_calls_tenant_status_eta", table_name="vessel_calls")
    op.drop_index("ix_vessel_calls_tenant_eta", table_name="vessel_calls")
    op.drop_table("vessel_calls")

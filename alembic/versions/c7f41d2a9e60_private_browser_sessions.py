"""Private anonymous browser sessions; isolate durable vendor consent by buyer."""

import sqlalchemy as sa

from alembic import op

revision = "c7f41d2a9e60"
down_revision = "b6e39f8a2c10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "browser_owners",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "browser_sessions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "owner_hash", sa.String(64), sa.ForeignKey("browser_owners.token_hash"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_browser_sessions_owner_hash", "browser_sessions", ["owner_hash"])
    op.add_column("vendors", sa.Column("owner_scope", sa.String(64), nullable=True))
    op.drop_index("ix_vendors_external_id", table_name="vendors")
    op.create_index("ix_vendors_external_id", "vendors", ["external_id"], unique=False)
    op.create_index("ix_vendors_owner_scope", "vendors", ["owner_scope"])
    with op.batch_alter_table("vendors") as batch:
        batch.create_unique_constraint("uq_vendor_owner_external", ["owner_scope", "external_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT count(*) FROM browser_owners")).scalar_one():
        raise RuntimeError("Private browser owners exist; a downgrade would remove ownership")
    if bind.execute(
        sa.text("SELECT count(*) FROM vendors WHERE owner_scope IS NOT NULL")
    ).scalar_one():
        raise RuntimeError("Scoped vendors exist; a downgrade would remove ownership")
    with op.batch_alter_table("vendors") as batch:
        batch.drop_constraint("uq_vendor_owner_external", type_="unique")
        batch.drop_index("ix_vendors_owner_scope")
        batch.drop_column("owner_scope")
    op.drop_index("ix_vendors_external_id", table_name="vendors")
    op.create_index("ix_vendors_external_id", "vendors", ["external_id"], unique=True)
    op.drop_index("ix_browser_sessions_owner_hash", table_name="browser_sessions")
    op.drop_table("browser_sessions")
    op.drop_table("browser_owners")

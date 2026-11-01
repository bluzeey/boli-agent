"""Persist exact outbound Twilio message correlation.

Revision ID: b6e39f8a2c10
Revises: a1b2c3d4e5f6
"""

import sqlalchemy as sa

from alembic import op

revision = "b6e39f8a2c10"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vendor_responses", sa.Column("twilio_message_sid", sa.String(64), nullable=True)
    )
    op.create_index(
        "ix_vendor_responses_twilio_message_sid",
        "vendor_responses",
        ["twilio_message_sid"],
        unique=True,
    )


def downgrade() -> None:
    with op.batch_alter_table("vendor_responses") as batch:
        batch.drop_index("ix_vendor_responses_twilio_message_sid")
        batch.drop_column("twilio_message_sid")

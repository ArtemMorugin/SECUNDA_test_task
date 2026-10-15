"""Persist delivery leases and enforce one webhook per payment.

Revision ID: 5b4767d79a21
Revises: 2e9bdcdbb931
"""

import sqlalchemy as sa

from alembic import op

revision = "5b4767d79a21"
down_revision = "2e9bdcdbb931"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("outbox", sa.Column("claim_token", sa.UUID(), nullable=True))
    op.add_column(
        "outbox",
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "webhook_deliveries",
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "webhook_deliveries",
        sa.Column("claim_token", sa.UUID(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_webhook_deliveries_payment_id",
        "webhook_deliveries",
        ["payment_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_webhook_deliveries_payment_id",
        "webhook_deliveries",
        type_="unique",
    )
    op.drop_column("webhook_deliveries", "claim_token")
    op.drop_column("webhook_deliveries", "locked_until")
    op.drop_column("outbox", "failed_at")
    op.drop_column("outbox", "claim_token")

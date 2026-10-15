
"""add document access grants and unique user names

Revision ID: 073246fce057
Revises: af590a1f7c40
Create Date: 2026-10-15 10:07:34.660884
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "073246fce057"
down_revision: Union[str, Sequence[str], None] = "af590a1f7c40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_access_grants",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("granted_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "user_id",
            name="uq_document_access_grant",
        ),
    )

    op.create_unique_constraint(
        "uq_users_name",
        "users",
        ["name"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_users_name",
        "users",
        type_="unique",
    )
    op.drop_table("document_access_grants")

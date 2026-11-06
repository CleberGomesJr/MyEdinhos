"""preferencias (estado do assistente de cadastro inicial, E2-22)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "preferencias",
        sa.Column("chave", sa.String(length=60), nullable=False),
        sa.Column("valor", sa.Text(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("chave", name=op.f("pk_preferencias")),
    )


def downgrade() -> None:
    op.drop_table("preferencias")

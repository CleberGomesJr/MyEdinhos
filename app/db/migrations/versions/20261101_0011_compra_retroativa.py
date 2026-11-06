"""compra retroativa: parcelas já pagas antes de serem lançadas (lancamentos.retroativo)

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Coluna nova direto (sem batch): não recria a tabela de lançamentos.
    op.add_column(
        "lancamentos",
        sa.Column("retroativo", sa.Boolean(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    with op.batch_alter_table("lancamentos") as batch_op:
        batch_op.drop_column("retroativo")

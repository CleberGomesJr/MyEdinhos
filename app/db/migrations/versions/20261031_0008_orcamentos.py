"""orcamentos (E3-07)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.db.types  # noqa: F401  (o autogenerate referencia Money/DecimalText)

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "orcamentos",
        sa.Column("categoria_id", sa.Integer(), nullable=False),
        sa.Column("competencia", sa.String(length=7), nullable=False),
        sa.Column("limite_centavos", app.db.types.Money(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(), nullable=False),
        sa.CheckConstraint("limite_centavos >= 0", name=op.f("ck_orcamentos_limite_nao_negativo")),
        sa.ForeignKeyConstraint(
            ["categoria_id"],
            ["categorias.id"],
            name=op.f("fk_orcamentos_categoria_id_categorias"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orcamentos")),
        sa.UniqueConstraint(
            "categoria_id", "competencia", name="uq_orcamentos_categoria_competencia"
        ),
    )


def downgrade() -> None:
    op.drop_table("orcamentos")

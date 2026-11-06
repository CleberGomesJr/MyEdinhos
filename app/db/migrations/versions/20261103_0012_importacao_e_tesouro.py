"""importação de extrato e corretora (E2-13, E4-11): id_externo; títulos do Tesouro (E5-05)

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.db.types  # noqa: F401

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Colunas e índices direto (sem batch): recriar as tabelas perderia o AUTOINCREMENT.
    op.add_column("lancamentos", sa.Column("id_externo", sa.String(length=80), nullable=True))
    op.create_index(op.f("ix_lancamentos_id_externo"), "lancamentos", ["id_externo"], unique=False)
    op.add_column("operacoes", sa.Column("id_externo", sa.String(length=80), nullable=True))
    op.create_index(op.f("ix_operacoes_id_externo"), "operacoes", ["id_externo"], unique=False)
    op.create_table(
        "titulos_tesouro",
        sa.Column("tipo", sa.String(length=60), nullable=False),
        sa.Column("vencimento", sa.Date(), nullable=False),
        sa.Column("data_base", sa.Date(), nullable=False),
        sa.Column("taxa_compra", app.db.types.DecimalText(), nullable=True),
        sa.Column("taxa_venda", app.db.types.DecimalText(), nullable=True),
        sa.Column("pu_compra", app.db.types.DecimalText(), nullable=True),
        sa.Column("pu_venda", app.db.types.DecimalText(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_titulos_tesouro")),
        sa.UniqueConstraint("tipo", "vencimento", name="uq_titulos_tesouro_tipo_vencimento"),
    )


def downgrade() -> None:
    op.drop_table("titulos_tesouro")
    op.drop_index(op.f("ix_operacoes_id_externo"), table_name="operacoes")
    op.drop_index(op.f("ix_lancamentos_id_externo"), table_name="lancamentos")
    with op.batch_alter_table("operacoes", table_kwargs={"sqlite_autoincrement": True}) as batch_op:
        batch_op.drop_column("id_externo")
    with op.batch_alter_table(
        "lancamentos", table_kwargs={"sqlite_autoincrement": True}
    ) as batch_op:
        batch_op.drop_column("id_externo")

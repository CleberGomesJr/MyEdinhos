"""objetivos e metas de alocação (E6-02); reserva de emergência em contas e ativos

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.db.types  # noqa: F401

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GRUPOS = ("renda_fixa", "acoes", "fiis", "exterior", "cripto", "outros")


def upgrade() -> None:
    op.create_table(
        "metas_alocacao",
        sa.Column(
            "grupo",
            sa.Enum(
                *GRUPOS, name="grupo_alocacao", native_enum=False, create_constraint=True, length=20
            ),
            nullable=False,
        ),
        sa.Column("percentual_alvo", app.db.types.DecimalText(), nullable=False),
        sa.Column("tolerancia_pp", app.db.types.DecimalText(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "percentual_alvo >= 0", name=op.f("ck_metas_alocacao_alvo_nao_negativo")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metas_alocacao")),
        sa.UniqueConstraint("grupo", name=op.f("uq_metas_alocacao_grupo")),
    )
    op.create_table(
        "objetivos",
        sa.Column("nome", sa.String(length=80), nullable=False),
        sa.Column("valor_alvo_centavos", app.db.types.Money(), nullable=False),
        sa.Column("prazo", sa.Date(), nullable=True),
        sa.Column("aporte_mensal_centavos", app.db.types.Money(), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("criado_em", sa.DateTime(), nullable=False),
        sa.Column("atualizado_em", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_objetivos")),
    )
    # Coluna nova direto (sem batch): não recria a tabela.
    op.add_column(
        "contas", sa.Column("reserva_emergencia", sa.Boolean(), server_default="0", nullable=False)
    )
    op.add_column(
        "ativos", sa.Column("reserva_emergencia", sa.Boolean(), server_default="0", nullable=False)
    )


def downgrade() -> None:
    with op.batch_alter_table("ativos") as batch_op:
        batch_op.drop_column("reserva_emergencia")
    with op.batch_alter_table("contas") as batch_op:
        batch_op.drop_column("reserva_emergencia")
    op.drop_table("objetivos")
    op.drop_table("metas_alocacao")

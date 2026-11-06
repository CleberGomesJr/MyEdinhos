"""seed: categorias padrão

Roda uma única vez: categorias apagadas pelo usuário não voltam. Nenhuma conta nem outro
dado do usuário é criado: o app começa vazio e o cadastro é feito pela interface.
cor_slot 1–8 = paleta categórica; 0 = cinza de "Outros".

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-26
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SAIDAS = [
    ("Moradia", 1, "house"),
    ("Mercado", 2, "shopping-cart"),
    ("Assinaturas", 3, "repeat"),
    ("Transporte", 4, "car"),
    ("Saúde", 5, "heart-pulse"),
    ("Educação", 6, "graduation-cap"),
    ("Lazer", 7, "party-popper"),
    ("Restaurantes", 8, "utensils"),
    ("Outros gastos", 0, "circle-ellipsis"),
]
ENTRADAS = [
    ("Salário", 3, "banknote"),
    ("Rendimentos", 1, "piggy-bank"),
    ("Renda extra", 4, "hand-coins"),
    ("Outras entradas", 0, "circle-ellipsis"),
]

categorias = sa.table(
    "categorias",
    sa.column("nome", sa.String),
    sa.column("tipo", sa.String),
    sa.column("cor_slot", sa.Integer),
    sa.column("icone", sa.String),
    sa.column("ativa", sa.Boolean),
    sa.column("criado_em", sa.DateTime),
    sa.column("atualizado_em", sa.DateTime),
)


def upgrade() -> None:
    agora = datetime.now(UTC).replace(tzinfo=None)
    carimbo = {"ativa": True, "criado_em": agora, "atualizado_em": agora}
    op.bulk_insert(
        categorias,
        [
            {"nome": nome, "tipo": tipo, "cor_slot": slot, "icone": icone, **carimbo}
            for tipo, lista in (("saida", SAIDAS), ("entrada", ENTRADAS))
            for nome, slot, icone in lista
        ],
    )


def downgrade() -> None:
    nomes = [nome for nome, _, _ in SAIDAS + ENTRADAS]
    op.execute(categorias.delete().where(categorias.c.nome.in_(nomes)))

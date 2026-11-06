"""Base declarativa dos modelos e campos comuns a toda tabela."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Nomes previsíveis para constraints: o Alembic precisa deles para alterar tabelas no SQLite.
CONVENCAO_NOMES = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def agora_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=CONVENCAO_NOMES)


class CamposPadrao:
    """`id`, `criado_em` e `atualizado_em` (UTC), presentes em toda tabela."""

    id: Mapped[int] = mapped_column(primary_key=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora_utc)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime, default=agora_utc, onupdate=agora_utc)

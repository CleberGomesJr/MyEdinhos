"""Objetivos e metas de alocação (E6-02). Regras de compra/venda ficam em `preferencias`."""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import CheckConstraint, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CamposPadrao
from app.db.types import DecimalText, EnumTexto, Money


class GrupoAlocacao(StrEnum):
    """Grupos da alocação-alvo (vault "07 - Perfil de Investidor"), juntando classes de ativo."""

    RENDA_FIXA = "renda_fixa"
    ACOES = "acoes"
    FIIS = "fiis"
    EXTERIOR = "exterior"
    CRIPTO = "cripto"
    OUTROS = "outros"

    @property
    def rotulo(self) -> str:
        return {
            "renda_fixa": "Renda fixa / Tesouro",
            "acoes": "Ações Brasil",
            "fiis": "FIIs",
            "exterior": "ETFs / BDRs / exterior",
            "cripto": "Cripto",
            "outros": "Outros",
        }[self.value]

    @property
    def cor_slot(self) -> int:
        return {"renda_fixa": 6, "acoes": 1, "fiis": 3, "exterior": 4, "cripto": 5, "outros": 0}[
            self.value
        ]


class MetaAlocacao(CamposPadrao, Base):
    __tablename__ = "metas_alocacao"
    __table_args__ = (CheckConstraint("percentual_alvo >= 0", name="alvo_nao_negativo"),)

    grupo: Mapped[GrupoAlocacao] = mapped_column(
        EnumTexto(GrupoAlocacao, "grupo_alocacao"), unique=True
    )
    percentual_alvo: Mapped[Decimal] = mapped_column(DecimalText)
    tolerancia_pp: Mapped[Decimal] = mapped_column(DecimalText)


class Objetivo(CamposPadrao, Base):
    """Objetivo de patrimônio com valor e prazo (E6-09). Reserva de emergência e renda
    passiva são objetivos próprios, calculados (não ficam aqui)."""

    __tablename__ = "objetivos"

    nome: Mapped[str] = mapped_column(String(80))
    valor_alvo_centavos: Mapped[int] = mapped_column(Money)
    prazo: Mapped[date | None]
    aporte_mensal_centavos: Mapped[int] = mapped_column(Money, default=0)
    descricao: Mapped[str | None] = mapped_column(Text)
    ativo: Mapped[bool] = mapped_column(default=True)

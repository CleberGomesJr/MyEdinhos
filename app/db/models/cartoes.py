from datetime import date
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CamposPadrao
from app.db.models.contas import Conta
from app.db.types import EnumTexto, Money


class CompraNoFechamento(StrEnum):
    """Para onde vai uma compra feita no próprio dia do fechamento (varia por emissor)."""

    FATURA_ATUAL = "fatura_atual"
    PROXIMA_FATURA = "proxima_fatura"

    @property
    def rotulo(self) -> str:
        return {
            "fatura_atual": "Entra na fatura que fecha nesse dia",
            "proxima_fatura": "Vai para a próxima fatura",
        }[self.value]


class Cartao(CamposPadrao, Base):
    __tablename__ = "cartoes"
    __table_args__ = (
        CheckConstraint("limite_centavos >= 0", name="limite_positivo"),
        CheckConstraint("dia_fechamento BETWEEN 1 AND 31", name="dia_fechamento"),
        CheckConstraint("dia_vencimento BETWEEN 1 AND 31", name="dia_vencimento"),
    )

    nome: Mapped[str] = mapped_column(String(60), unique=True)
    bandeira: Mapped[str | None] = mapped_column(String(30))
    limite_centavos: Mapped[int] = mapped_column(Money)
    dia_fechamento: Mapped[int]
    dia_vencimento: Mapped[int]
    conta_pagamento_id: Mapped[int] = mapped_column(ForeignKey("contas.id", ondelete="RESTRICT"))
    compra_no_fechamento: Mapped[CompraNoFechamento] = mapped_column(
        EnumTexto(CompraNoFechamento, "compra_no_fechamento"),
        default=CompraNoFechamento.FATURA_ATUAL,
    )
    ativo: Mapped[bool] = mapped_column(default=True)

    conta_pagamento: Mapped[Conta] = relationship()


class Fatura(CamposPadrao, Base):
    """Fatura de um cartão, identificada pelo mês de vencimento (`referencia` = AAAA-MM).

    Total, valor pago e situação são calculados a partir dos lançamentos com `fatura_id`.
    """

    __tablename__ = "faturas"
    __table_args__ = (
        UniqueConstraint("cartao_id", "referencia", name="uq_faturas_cartao_referencia"),
    )

    cartao_id: Mapped[int] = mapped_column(ForeignKey("cartoes.id", ondelete="RESTRICT"))
    referencia: Mapped[str] = mapped_column(String(7))
    data_fechamento: Mapped[date]
    data_vencimento: Mapped[date]

    cartao: Mapped[Cartao] = relationship()

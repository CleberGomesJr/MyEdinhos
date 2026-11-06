from datetime import date
from enum import StrEnum

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CamposPadrao
from app.db.types import EnumTexto, Money


class TipoConta(StrEnum):
    CORRENTE = "corrente"
    POUPANCA = "poupanca"
    DINHEIRO = "dinheiro"
    CORRETORA = "corretora"

    @property
    def rotulo(self) -> str:
        return {
            "corrente": "Conta corrente",
            "poupanca": "Poupança",
            "dinheiro": "Dinheiro",
            "corretora": "Corretora",
        }[self.value]


class Conta(CamposPadrao, Base):
    """Conta de dinheiro. Cartões de crédito ficam em `cartoes` (E2-12), não aqui."""

    __tablename__ = "contas"

    nome: Mapped[str] = mapped_column(String(60), unique=True)
    tipo: Mapped[TipoConta] = mapped_column(EnumTexto(TipoConta, "tipo_conta"))
    # Saldo no início de `data_saldo_inicial`; lançamentos a partir dessa data movem o saldo.
    saldo_inicial_centavos: Mapped[int] = mapped_column(Money, default=0)
    data_saldo_inicial: Mapped[date]
    ativa: Mapped[bool] = mapped_column(default=True)
    # Faz parte da reserva de emergência (E6-09); entra em "Renda fixa" na alocação (E6-03).
    reserva_emergencia: Mapped[bool] = mapped_column(default=False, server_default="0")

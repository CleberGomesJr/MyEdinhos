from datetime import date
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CamposPadrao
from app.db.models.cartoes import Cartao, Fatura
from app.db.models.categorias import Categoria
from app.db.models.contas import Conta
from app.db.types import EnumTexto, Money


class TipoLancamento(StrEnum):
    ENTRADA = "entrada"
    SAIDA = "saida"
    TRANSFERENCIA = "transferencia"

    @property
    def rotulo(self) -> str:
        return {"entrada": "Entrada", "saida": "Saída", "transferencia": "Transferência"}[
            self.value
        ]


class Frequencia(StrEnum):
    SEMANAL = "semanal"
    MENSAL = "mensal"
    ANUAL = "anual"

    @property
    def rotulo(self) -> str:
        return {"semanal": "Toda semana", "mensal": "Todo mês", "anual": "Todo ano"}[self.value]


class Recorrencia(CamposPadrao, Base):
    """Regra que gera lançamentos pendentes. O dia (do mês/da semana) vem de `inicio`."""

    __tablename__ = "recorrencias"
    __table_args__ = (
        CheckConstraint("valor_centavos > 0", name="valor_positivo"),
        CheckConstraint("tipo <> 'transferencia'", name="sem_transferencia"),
        CheckConstraint("(conta_id IS NULL) <> (cartao_id IS NULL)", name="conta_ou_cartao"),
    )

    descricao: Mapped[str] = mapped_column(String(120))
    tipo: Mapped[TipoLancamento] = mapped_column(EnumTexto(TipoLancamento, "tipo_recorrencia"))
    valor_centavos: Mapped[int] = mapped_column(Money)
    conta_id: Mapped[int | None] = mapped_column(ForeignKey("contas.id", ondelete="RESTRICT"))
    cartao_id: Mapped[int | None] = mapped_column(ForeignKey("cartoes.id", ondelete="RESTRICT"))
    categoria_id: Mapped[int] = mapped_column(ForeignKey("categorias.id", ondelete="RESTRICT"))
    frequencia: Mapped[Frequencia] = mapped_column(EnumTexto(Frequencia, "frequencia"))
    inicio: Mapped[date]
    fim: Mapped[date | None]
    # Até onde já gerou lançamentos: ocorrências excluídas pelo usuário não voltam.
    gerado_ate: Mapped[date]
    ativa: Mapped[bool] = mapped_column(default=True)

    conta: Mapped[Conta | None] = relationship()
    cartao: Mapped[Cartao | None] = relationship()
    categoria: Mapped[Categoria] = relationship()


class Lancamento(CamposPadrao, Base):
    __tablename__ = "lancamentos"
    __table_args__ = (
        CheckConstraint("valor_centavos > 0", name="valor_positivo"),
        # Compra/crédito no cartão: só cartao_id. Pagamento de fatura: conta_id + cartao_id.
        CheckConstraint("conta_id IS NOT NULL OR cartao_id IS NOT NULL", name="tem_origem"),
        CheckConstraint(
            "(tipo = 'transferencia') = (conta_destino_id IS NOT NULL OR "
            "(conta_id IS NOT NULL AND cartao_id IS NOT NULL))",
            name="destino_so_em_transferencia",
        ),
        CheckConstraint(
            "conta_destino_id IS NULL OR cartao_id IS NULL", name="destino_conta_ou_cartao"
        ),
        CheckConstraint("(cartao_id IS NULL) = (fatura_id IS NULL)", name="fatura_com_cartao"),
        CheckConstraint(
            "conta_destino_id IS NULL OR conta_destino_id <> conta_id", name="destino_diferente"
        ),
        CheckConstraint(
            "(tipo = 'transferencia') = (categoria_id IS NULL)", name="categoria_fora_transferencia"
        ),
        Index("ix_lancamentos_conta_data", "conta_id", "data"),
        Index("ix_lancamentos_categoria_competencia", "categoria_id", "competencia"),
        # Sem reaproveitar ids: o "Desfazer" reinsere a linha com o id original.
        {"sqlite_autoincrement": True},
    )

    data: Mapped[date]
    competencia: Mapped[str] = mapped_column(String(7), index=True)  # AAAA-MM
    tipo: Mapped[TipoLancamento] = mapped_column(EnumTexto(TipoLancamento, "tipo_lancamento"))
    valor_centavos: Mapped[int] = mapped_column(Money)  # sempre positivo; o sinal vem do tipo
    conta_id: Mapped[int | None] = mapped_column(ForeignKey("contas.id", ondelete="RESTRICT"))
    conta_destino_id: Mapped[int | None] = mapped_column(
        ForeignKey("contas.id", ondelete="RESTRICT")
    )
    categoria_id: Mapped[int | None] = mapped_column(
        ForeignKey("categorias.id", ondelete="RESTRICT")
    )
    cartao_id: Mapped[int | None] = mapped_column(ForeignKey("cartoes.id", ondelete="RESTRICT"))
    fatura_id: Mapped[int | None] = mapped_column(
        ForeignKey("faturas.id", ondelete="RESTRICT"), index=True
    )
    descricao: Mapped[str] = mapped_column(String(120))
    pago: Mapped[bool] = mapped_column(default=True)
    # Compra retroativa: parcela que já foi paga antes de ser lançada aqui. Conta nos gastos do
    # mês da sua data (histórico), mas não move o saldo de hoje nem deixa a fatura em aberto.
    retroativo: Mapped[bool] = mapped_column(default=False, server_default="0")
    # Identifica a linha do extrato importado (E2-13): reimportar o arquivo não duplica.
    id_externo: Mapped[str | None] = mapped_column(String(80), index=True)
    vencimento: Mapped[date | None]
    recorrencia_id: Mapped[int | None] = mapped_column(
        ForeignKey("recorrencias.id", ondelete="SET NULL"), index=True
    )
    parcela_grupo_id: Mapped[str | None] = mapped_column(String(32), index=True)
    parcela_n: Mapped[int | None]
    parcela_total: Mapped[int | None]
    observacao: Mapped[str | None] = mapped_column(Text)

    conta: Mapped[Conta | None] = relationship(foreign_keys=[conta_id])
    conta_destino: Mapped[Conta | None] = relationship(foreign_keys=[conta_destino_id])
    categoria: Mapped[Categoria | None] = relationship()
    cartao: Mapped[Cartao | None] = relationship()
    fatura: Mapped[Fatura | None] = relationship()
    recorrencia: Mapped[Recorrencia | None] = relationship()

    @property
    def pagamento_de_fatura(self) -> bool:
        return self.tipo == TipoLancamento.TRANSFERENCIA and self.cartao_id is not None

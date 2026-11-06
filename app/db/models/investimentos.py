"""Carteira de investimentos (E4) e dados de mercado (E5). Modelo no vault "03 - Modelo de Dados".

Quantidades e preços unitários são `Decimal` (DecimalText); valores em reais, centavos (Money).
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CamposPadrao, agora_utc
from app.db.models.contas import Conta
from app.db.types import DecimalText, EnumTexto, Money


class ClasseAtivo(StrEnum):
    ACAO = "acao"
    FII = "fii"
    ETF = "etf"
    BDR = "bdr"
    RENDA_FIXA = "renda_fixa"
    TESOURO = "tesouro"
    CRIPTO = "cripto"
    FUNDO = "fundo"
    OUTRO = "outro"

    @property
    def rotulo(self) -> str:
        return {
            "acao": "Ações",
            "fii": "FIIs",
            "etf": "ETFs",
            "bdr": "BDRs",
            "renda_fixa": "Renda fixa",
            "tesouro": "Tesouro Direto",
            "cripto": "Cripto",
            "fundo": "Fundos",
            "outro": "Outros",
        }[self.value]

    @property
    def cor_slot(self) -> int:
        """Cor fixa da classe nos gráficos (paleta categórica; 0 = cinza)."""
        return {
            "acao": 1, "fii": 3, "etf": 4, "bdr": 7, "renda_fixa": 6,
            "tesouro": 2, "cripto": 5, "fundo": 8, "outro": 0,
        }[self.value]  # fmt: skip

    @property
    def tem_cotacao(self) -> bool:
        """Negociado em bolsa/mercado: a cotação vem das fontes (E5)."""
        return self in (ClasseAtivo.ACAO, ClasseAtivo.FII, ClasseAtivo.ETF, ClasseAtivo.BDR,
                        ClasseAtivo.CRIPTO)  # fmt: skip

    @property
    def renda_fixa(self) -> bool:
        return self in (ClasseAtivo.RENDA_FIXA, ClasseAtivo.TESOURO)


class Indexador(StrEnum):
    CDI = "cdi"
    SELIC = "selic"
    IPCA = "ipca"
    PREFIXADO = "prefixado"

    @property
    def rotulo(self) -> str:
        return {
            "cdi": "% do CDI",
            "selic": "% da Selic",
            "ipca": "IPCA +",
            "prefixado": "Prefixado",
        }[self.value]


class Ativo(CamposPadrao, Base):
    __tablename__ = "ativos"

    ticker: Mapped[str] = mapped_column(String(20), unique=True)
    nome: Mapped[str] = mapped_column(String(120))
    classe: Mapped[ClasseAtivo] = mapped_column(EnumTexto(ClasseAtivo, "classe_ativo"))
    setor: Mapped[str | None] = mapped_column(String(60))
    # Renda fixa: taxa em % a.a. (prefixado, IPCA +) ou % do índice (CDI, Selic).
    indexador: Mapped[Indexador | None] = mapped_column(EnumTexto(Indexador, "indexador"))
    taxa: Mapped[Decimal | None] = mapped_column(DecimalText)
    vencimento: Mapped[date | None]
    ativo: Mapped[bool] = mapped_column(default=True)
    reserva_emergencia: Mapped[bool] = mapped_column(default=False, server_default="0")


class TipoOperacao(StrEnum):
    POSICAO_INICIAL = "posicao_inicial"
    COMPRA = "compra"
    VENDA = "venda"
    DIVIDENDO = "dividendo"
    JCP = "jcp"
    RENDIMENTO = "rendimento"
    DESDOBRAMENTO = "desdobramento"
    BONIFICACAO = "bonificacao"

    @property
    def rotulo(self) -> str:
        return {
            "posicao_inicial": "Posição inicial",
            "compra": "Compra",
            "venda": "Venda",
            "dividendo": "Dividendo",
            "jcp": "JCP",
            "rendimento": "Rendimento",
            "desdobramento": "Desdobramento",
            "bonificacao": "Bonificação",
        }[self.value]

    @property
    def provento(self) -> bool:
        return self in (TipoOperacao.DIVIDENDO, TipoOperacao.JCP, TipoOperacao.RENDIMENTO)


class Operacao(CamposPadrao, Base):
    """Compra, venda, provento ou evento do ativo.

    - compra/posição inicial: `valor_total` = quantidade × preço + taxas (custo);
    - venda: `valor_total` = quantidade × preço − taxas (o que entrou);
    - provento: `valor_total` = valor recebido (líquido); quantidade é opcional;
    - desdobramento: `quantidade` = fator (2 = cada ação vira 2; 0,1 = grupamento 10:1);
    - bonificação: `quantidade` = ações recebidas, `preco_unitario` = custo atribuído (pode ser 0).
    Com `conta_id`, compra tira da conta e venda/provento põem na conta (E4-03).
    """

    __tablename__ = "operacoes"
    __table_args__ = (
        CheckConstraint("valor_total_centavos >= 0", name="valor_nao_negativo"),
        CheckConstraint("taxas_centavos >= 0", name="taxas_nao_negativas"),
        {"sqlite_autoincrement": True},
    )

    ativo_id: Mapped[int] = mapped_column(ForeignKey("ativos.id", ondelete="RESTRICT"), index=True)
    data: Mapped[date]
    tipo: Mapped[TipoOperacao] = mapped_column(EnumTexto(TipoOperacao, "tipo_operacao"))
    quantidade: Mapped[Decimal | None] = mapped_column(DecimalText)
    preco_unitario: Mapped[Decimal | None] = mapped_column(DecimalText)
    taxas_centavos: Mapped[int] = mapped_column(Money, default=0)
    valor_total_centavos: Mapped[int] = mapped_column(Money, default=0)
    conta_id: Mapped[int | None] = mapped_column(
        ForeignKey("contas.id", ondelete="RESTRICT"), index=True
    )
    observacao: Mapped[str | None] = mapped_column(Text)
    # Identifica a linha do CSV da corretora (E4-11): reimportar o arquivo não duplica.
    id_externo: Mapped[str | None] = mapped_column(String(80), index=True)

    ativo: Mapped[Ativo] = relationship()
    conta: Mapped[Conta | None] = relationship()


class Cotacao(Base):
    """Fechamento diário. `fonte` = brapi, yahoo ou manual."""

    __tablename__ = "cotacoes"
    __table_args__ = (UniqueConstraint("ativo_id", "data", name="uq_cotacoes_ativo_data"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ativo_id: Mapped[int] = mapped_column(ForeignKey("ativos.id", ondelete="CASCADE"))
    data: Mapped[date]
    fechamento: Mapped[Decimal] = mapped_column(DecimalText)
    fonte: Mapped[str] = mapped_column(String(20))
    obtido_em: Mapped[datetime] = mapped_column(DateTime, default=agora_utc)


class TipoProvento(StrEnum):
    DIVIDENDO = "dividendo"
    JCP = "jcp"
    RENDIMENTO = "rendimento"

    @property
    def rotulo(self) -> str:
        return {"dividendo": "Dividendo", "jcp": "JCP", "rendimento": "Rendimento"}[self.value]


class ProventoAnunciado(Base):
    """Provento divulgado pela fonte (E5-12). Vira operação só quando o usuário confirma."""

    __tablename__ = "proventos_anunciados"
    __table_args__ = (
        UniqueConstraint("ativo_id", "tipo", "data_com", name="uq_proventos_anunciados_ativo"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ativo_id: Mapped[int] = mapped_column(ForeignKey("ativos.id", ondelete="CASCADE"))
    tipo: Mapped[TipoProvento] = mapped_column(EnumTexto(TipoProvento, "tipo_provento"))
    data_com: Mapped[date]  # último dia com direito
    data_pagamento: Mapped[date | None]
    valor_por_unidade: Mapped[Decimal] = mapped_column(DecimalText)
    fonte: Mapped[str] = mapped_column(String(20))
    obtido_em: Mapped[datetime] = mapped_column(DateTime, default=agora_utc)
    operacao_id: Mapped[int | None] = mapped_column(ForeignKey("operacoes.id", ondelete="SET NULL"))
    ignorado: Mapped[bool] = mapped_column(default=False)

    ativo: Mapped[Ativo] = relationship()


class TituloTesouro(Base):
    """Último preço e taxa de cada título à venda (E5-05). A tabela é refeita a cada busca."""

    __tablename__ = "titulos_tesouro"
    __table_args__ = (
        UniqueConstraint("tipo", "vencimento", name="uq_titulos_tesouro_tipo_vencimento"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(String(60))
    vencimento: Mapped[date]
    data_base: Mapped[date]
    taxa_compra: Mapped[Decimal | None] = mapped_column(DecimalText)
    taxa_venda: Mapped[Decimal | None] = mapped_column(DecimalText)
    pu_compra: Mapped[Decimal | None] = mapped_column(DecimalText)
    pu_venda: Mapped[Decimal] = mapped_column(DecimalText)


class IndiceMacro(Base):
    """Série do Banco Central (E5-04): `cdi` e `selic` em % ao dia, `ipca` em % no mês,
    `selic_meta` em % ao ano."""

    __tablename__ = "indices_macro"
    __table_args__ = (UniqueConstraint("serie", "data", name="uq_indices_macro_serie_data"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    serie: Mapped[str] = mapped_column(String(20))
    data: Mapped[date]
    valor: Mapped[Decimal] = mapped_column(DecimalText)
    fonte: Mapped[str] = mapped_column(String(20))

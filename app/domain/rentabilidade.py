"""Rentabilidade da carteira (E6-05): XIRR × CDI, IPCA e Ibovespa no mesmo período.

XIRR (taxa interna de retorno com datas) considera quando cada real entrou e saiu:
compras e posição inicial são saídas; vendas e proventos, entradas; o valor de hoje
fecha a conta. Tudo em Decimal (sem float). Antes de 1 ano, mostra o retorno do período
(anualizar poucos meses exagera); a partir de 1 ano, ao ano.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import IndiceMacro, TipoOperacao
from app.domain import carteira

UM = Decimal(1)


def xirr(fluxos: list[tuple[date, int]]) -> Decimal | None:
    """Taxa ao ano (0,12 = 12%) que zera o valor presente dos fluxos. Bissecção."""
    if not fluxos or not any(v < 0 for _, v in fluxos) or not any(v > 0 for _, v in fluxos):
        return None
    inicio = min(d for d, _ in fluxos)
    # Um termo por data: com anos de operações, a conta fica dezenas de vezes mais rápida.
    por_data: dict[date, int] = {}
    for dia, valor in fluxos:
        por_data[dia] = por_data.get(dia, 0) + valor
    prazos = [(Decimal((d - inicio).days) / 365, Decimal(v)) for d, v in por_data.items() if v]

    def valor_presente(taxa: Decimal) -> Decimal:
        return sum((v / (UM + taxa) ** t for t, v in prazos), Decimal(0))

    with localcontext() as contexto:
        contexto.prec = 20  # sobra para uma taxa com 8 casas e deixa a potência mais rápida
        baixo, alto = Decimal("-0.9999"), Decimal(100)
        if valor_presente(baixo) * valor_presente(alto) > 0:
            return None
        for _ in range(200):
            meio = (baixo + alto) / 2
            if valor_presente(meio) > 0:
                baixo = meio
            else:
                alto = meio
            if alto - baixo < Decimal("1e-8"):
                break
        return (baixo + alto) / 2


def fluxos_da_carteira(sessao: Session, hoje: date) -> tuple[list[tuple[date, int]], int]:
    """([(data, valor)], patrimônio de hoje). Sinal do ponto de vista do investidor."""
    fluxos = []
    for op in carteira.operacoes(sessao, hoje):
        if op.tipo in (TipoOperacao.COMPRA, TipoOperacao.POSICAO_INICIAL):
            fluxos.append((op.data, -op.valor_total_centavos))
        elif op.tipo == TipoOperacao.VENDA or op.tipo.provento:
            fluxos.append((op.data, op.valor_total_centavos))
        # Desdobramento e bonificação não movem dinheiro.
    patrimonio = carteira.montar(sessao, hoje).patrimonio
    if patrimonio:
        fluxos.append((hoje, patrimonio))
    return fluxos, patrimonio


def _acumulado_diario(sessao: Session, serie: str, inicio: date, fim: date) -> Decimal | None:
    valores = list(
        sessao.scalars(
            select(IndiceMacro.valor)
            .where(IndiceMacro.serie == serie, IndiceMacro.data > inicio, IndiceMacro.data <= fim)
            .order_by(IndiceMacro.data)
        )
    )
    if not valores:
        return None
    fator = UM
    for valor in valores:
        fator *= UM + valor / 100
    return fator - 1


def _ipca(sessao: Session, inicio: date, fim: date) -> Decimal | None:
    """IPCA dos meses inteiros depois do início (o índice sai mês a mês)."""
    meses = list(
        sessao.scalars(
            select(IndiceMacro.valor)
            .where(IndiceMacro.serie == "ipca", IndiceMacro.data > inicio, IndiceMacro.data <= fim)
            .order_by(IndiceMacro.data)
        )
    )
    if not meses:
        return None
    fator = UM
    for valor in meses:
        fator *= UM + valor / 100
    return fator - 1


def _ibovespa(sessao: Session, inicio: date, fim: date) -> Decimal | None:
    def ponto(dia: date, depois: bool) -> Decimal | None:
        consulta = select(IndiceMacro.valor).where(IndiceMacro.serie == "ibovespa")
        if depois:
            consulta = consulta.where(IndiceMacro.data >= dia).order_by(IndiceMacro.data)
        else:
            consulta = consulta.where(IndiceMacro.data <= dia).order_by(IndiceMacro.data.desc())
        return sessao.scalar(consulta.limit(1))

    primeiro, ultimo = ponto(inicio, True), ponto(fim, False)
    if not primeiro or not ultimo:
        return None
    return ultimo / primeiro - 1


@dataclass(frozen=True)
class Comparacao:
    nome: str
    retorno: Decimal | None  # no período, em % (já convertido para a base da tela)


@dataclass(frozen=True)
class Rentabilidade:
    inicio: date | None
    dias: int
    taxa_anual: Decimal | None  # XIRR, em %
    patrimonio: int
    comparacoes: list[Comparacao]

    @property
    def anualizado(self) -> bool:
        return self.dias >= 365

    @property
    def retorno(self) -> Decimal | None:
        """Na base da tela: ao ano (≥ 1 ano) ou no período."""
        if self.taxa_anual is None:
            return None
        if self.anualizado:
            return self.taxa_anual
        return ((UM + self.taxa_anual / 100) ** (Decimal(self.dias) / 365) - 1) * 100


def _na_base(acumulado: Decimal | None, dias: int, anualizar: bool) -> Decimal | None:
    if acumulado is None:
        return None
    if not anualizar:
        return acumulado * 100
    return ((UM + acumulado) ** (Decimal(365) / Decimal(dias)) - 1) * 100


def calcular(sessao: Session, hoje: date) -> Rentabilidade:
    fluxos, patrimonio = fluxos_da_carteira(sessao, hoje)
    if not fluxos:
        return Rentabilidade(None, 0, None, 0, [])
    inicio = min(d for d, _ in fluxos)
    dias = (hoje - inicio).days
    taxa = xirr(fluxos) if dias >= 30 else None
    anualizar = dias >= 365
    comparacoes = [
        Comparacao(
            "CDI", _na_base(_acumulado_diario(sessao, "cdi", inicio, hoje), dias, anualizar)
        ),
        Comparacao("IPCA", _na_base(_ipca(sessao, inicio, hoje), dias, anualizar)),
        Comparacao("Ibovespa", _na_base(_ibovespa(sessao, inicio, hoje), dias, anualizar)),
    ]
    return Rentabilidade(
        inicio, dias, taxa * 100 if taxa is not None else None, patrimonio, comparacoes
    )

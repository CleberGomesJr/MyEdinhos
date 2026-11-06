"""Proventos ⭐ (E4-10, E4-13, E4-14, E5-12): recebidos, a receber, rankings, yield e meta.

Recebido = operação de dividendo/JCP/rendimento lançada (o usuário confirma).
Anunciado = o que a fonte de mercado divulgou (`proventos_anunciados`); nada vira
operação sozinho: aparece como sugestão para o usuário lançar ou ignorar.
"""

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.db.models import Ativo, ClasseAtivo, Operacao, ProventoAnunciado, TipoOperacao
from app.domain import carteira, preferencias
from app.domain.ativos import centavos
from app.domain.competencia import Competencia
from app.domain.resumos import meses_ate

TIPOS = [TipoOperacao.DIVIDENDO, TipoOperacao.JCP, TipoOperacao.RENDIMENTO]
META = "meta_renda_passiva"  # centavos por mês
JANELA_LANCADO = 45  # dias depois da data com em que um provento lançado "cobre" o anunciado


def recebidos(sessao: Session, inicio: date, fim: date) -> list[Operacao]:
    return list(
        sessao.scalars(
            select(Operacao)
            .options(joinedload(Operacao.ativo), joinedload(Operacao.conta))
            .where(Operacao.tipo.in_(TIPOS), Operacao.data >= inicio, Operacao.data <= fim)
            .order_by(Operacao.data.desc(), Operacao.id.desc())
        )
    )


def por_mes(
    sessao: Session, ate: Competencia, quantidade: int = 12
) -> list[tuple[Competencia, int]]:
    meses = meses_ate(ate, quantidade)
    mes_da_data = func.strftime("%Y-%m", Operacao.data)
    somas = dict(
        list(
            sessao.execute(
                select(mes_da_data, func.sum(Operacao.valor_total_centavos))
                .where(
                    Operacao.tipo.in_(TIPOS),
                    Operacao.data >= meses[0].primeiro_dia,
                    Operacao.data <= meses[-1].ultimo_dia,
                )
                .group_by(mes_da_data)
            ).tuples()
        )
    )
    return [(mes, somas.get(str(mes), 0)) for mes in meses]


@dataclass(frozen=True)
class ItemRanking:
    nome: str
    slot: int
    valor: int
    percentual: Decimal
    ativo: Ativo | None = None


def ranking_ativos(sessao: Session, inicio: date, fim: date, limite: int = 8) -> list[ItemRanking]:
    """Ativos que mais pagaram no período (cor = classe do ativo)."""
    somas = carteira.proventos_por_ativo(sessao, inicio, fim)
    total = sum(somas.values())
    if not total:
        return []
    ativos = {a.id: a for a in sessao.scalars(select(Ativo).where(Ativo.id.in_(list(somas))))}
    itens = sorted(somas.items(), key=lambda item: -item[1])
    ranking = [
        ItemRanking(ativos[aid].ticker, ativos[aid].classe.cor_slot, valor,
                    Decimal(valor * 100) / total, ativos[aid])
        for aid, valor in itens[:limite]
    ]  # fmt: skip
    resto = sum(valor for _, valor in itens[limite:])
    if resto:
        ranking.append(ItemRanking("Outros", 0, resto, Decimal(resto * 100) / total))
    return ranking


def por_classe(sessao: Session, inicio: date, fim: date) -> list[ItemRanking]:
    somas: dict[ClasseAtivo, int] = {}
    linhas = sessao.execute(
        select(Ativo.classe, func.sum(Operacao.valor_total_centavos))
        .join(Ativo, Ativo.id == Operacao.ativo_id)
        .where(Operacao.tipo.in_(TIPOS), Operacao.data >= inicio, Operacao.data <= fim)
        .group_by(Ativo.classe)
    )
    for classe, valor in linhas:
        somas[classe] = valor
    total = sum(somas.values())
    return [
        ItemRanking(classe.rotulo, classe.cor_slot, valor, Decimal(valor * 100) / total)
        for classe, valor in sorted(somas.items(), key=lambda item: -item[1])
    ]


# ───────── Dividend yield atual ─────────


def proventos_por_unidade_12m(sessao: Session, hoje: date) -> dict[int, Decimal]:
    """Soma do valor por cota/ação com data com nos últimos 12 meses (fonte de mercado)."""
    linhas = sessao.execute(
        select(ProventoAnunciado.ativo_id, ProventoAnunciado.valor_por_unidade).where(
            ProventoAnunciado.data_com > hoje - timedelta(days=365),
            ProventoAnunciado.data_com <= hoje,
        )
    )
    somas: dict[int, Decimal] = {}
    for ativo_id, valor in linhas:
        somas[ativo_id] = somas.get(ativo_id, Decimal(0)) + valor
    return somas


def dividend_yield(
    linha: carteira.LinhaCarteira, por_unidade: dict[int, Decimal]
) -> Decimal | None:
    """DY atual = proventos por unidade em 12 meses ÷ cotação de hoje, em %.
    Sem dados da fonte, usa o que foi recebido ÷ quantidade atual."""
    if linha.cotacao is None or not linha.cotacao.fechamento or not linha.posicao.aberta:
        return None
    unidade = por_unidade.get(linha.ativo.id)
    if unidade is None and linha.proventos_12m:
        unidade = Decimal(linha.proventos_12m) / 100 / linha.posicao.quantidade
    if not unidade:
        return None
    return unidade * 100 / linha.cotacao.fechamento


# ───────── A receber e sugestões (E5-12) ─────────


@dataclass(frozen=True)
class Previsto:
    anuncio: ProventoAnunciado
    quantidade: Decimal
    valor: int  # estimado: quantidade × valor por unidade

    @property
    def data_referencia(self) -> date:
        return self.anuncio.data_pagamento or self.anuncio.data_com


def _datas_de_provento(sessao: Session) -> dict[int, list[date]]:
    """{ativo: datas dos proventos lançados, em ordem}."""
    linhas = sessao.execute(
        select(Operacao.ativo_id, Operacao.data)
        .where(Operacao.tipo.in_(TIPOS))
        .order_by(Operacao.data)
    )
    datas: dict[int, list[date]] = {}
    for ativo_id, dia in linhas:
        datas.setdefault(ativo_id, []).append(dia)
    return datas


def _ja_lancado(anuncio: ProventoAnunciado, lancados: dict[int, list[date]]) -> bool:
    fim = (anuncio.data_pagamento or anuncio.data_com) + timedelta(days=JANELA_LANCADO)
    datas = lancados.get(anuncio.ativo_id, [])
    # Há provento lançado depois da data com e até o fim da janela?
    return bisect_right(datas, fim) > bisect_right(datas, anuncio.data_com)


def anunciados(sessao: Session, hoje: date) -> tuple[list[Previsto], list[Previsto]]:
    """(a receber, sugestões para lançar).

    A receber: pagamento hoje ou depois (ou data com futura), para quem tinha (ou tem) posição.
    Sugestão: já pago (ou sem data de pagamento) nos últimos 12 meses, com posição na data
    com, ainda sem provento lançado nem ignorado.
    """
    linhas = list(
        sessao.scalars(
            select(ProventoAnunciado)
            .options(joinedload(ProventoAnunciado.ativo))
            .where(
                ProventoAnunciado.operacao_id.is_(None),
                ~ProventoAnunciado.ignorado,
                ProventoAnunciado.data_com > hoje - timedelta(days=365),
            )
            .order_by(ProventoAnunciado.data_com)
        )
    )
    quantidades = carteira.quantidades_em(sessao, (min(a.data_com, hoje) for a in linhas))
    lancados = _datas_de_provento(sessao)
    a_receber, sugestoes = [], []
    for anuncio in linhas:
        quantidade = quantidades[min(anuncio.data_com, hoje)].get(anuncio.ativo_id, Decimal(0))
        if quantidade <= 0:
            continue
        previsto = Previsto(anuncio, quantidade, centavos(quantidade * anuncio.valor_por_unidade))
        if previsto.valor <= 0:
            continue
        futuro = anuncio.data_com >= hoje or (
            anuncio.data_pagamento is not None and anuncio.data_pagamento >= hoje
        )
        if futuro:
            a_receber.append(previsto)
        elif not _ja_lancado(anuncio, lancados):
            sugestoes.append(previsto)
    a_receber.sort(key=lambda p: p.data_referencia)
    sugestoes.sort(key=lambda p: p.data_referencia, reverse=True)
    return a_receber, sugestoes


def ignorar(sessao: Session, anuncio: ProventoAnunciado) -> None:
    anuncio.ignorado = True
    sessao.flush()


# ───────── Meta de renda passiva (E4-13) ─────────


@dataclass(frozen=True)
class Meta:
    mensal: int  # centavos por mês
    media_12m: int
    yield_on_cost: Decimal | None  # da carteira, % a.a.

    @property
    def percentual(self) -> Decimal:
        return Decimal(self.media_12m * 100) / self.mensal if self.mensal else Decimal(0)

    @property
    def falta_mensal(self) -> int:
        return max(self.mensal - self.media_12m, 0)

    @property
    def aporte_necessario(self) -> int | None:
        """Projeção simples: quanto investir a mais, rendendo o yield on cost atual, para a
        renda anual cobrir a meta. Premissa: o yield se mantém (não é promessa)."""
        if not self.falta_mensal or not self.yield_on_cost or self.yield_on_cost <= 0:
            return None
        return carteira._inteiro(Decimal(self.falta_mensal * 12 * 100) / self.yield_on_cost)


def meta(sessao: Session, proventos_12m: int, yield_on_cost: Decimal | None) -> Meta | None:
    valor = preferencias.obter(sessao, META)
    if not valor or not int(valor):
        return None
    return Meta(int(valor), proventos_12m // 12, yield_on_cost)


def definir_meta(sessao: Session, centavos_por_mes: int | None) -> None:
    preferencias.definir(sessao, META, str(centavos_por_mes or 0))

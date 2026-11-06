"""Posição, preço médio, valor de mercado e resultados da carteira (E4-04 a E4-09).

Preço médio pelo método usual no Brasil (o da Receita): compras somam ao custo; a venda
baixa o custo pelo preço médio e não muda o preço médio do que sobra. O resultado
realizado é informativo — não substitui o cálculo de imposto de um contador.
Custos são guardados como Decimal em centavos durante o cálculo e arredondados só no fim.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.db.models import Ativo, ClasseAtivo, Cotacao, Indexador, IndiceMacro, Operacao
from app.db.models import TipoOperacao as T
from app.domain.competencia import Competencia
from app.domain.resumos import dia_de_referencia, meses_ate

ZERO = Decimal(0)
MAX_CLASSES = 5  # alocação: 5 classes + "Outras" (E4-08)
MARGEM_PRECO_TESOURO = Decimal("0.25")


def _inteiro(valor: Decimal) -> int:
    return int(valor.quantize(Decimal(1), rounding="ROUND_HALF_EVEN"))


@dataclass
class Posicao:
    ativo: Ativo
    quantidade: Decimal = ZERO
    custo: Decimal = ZERO  # centavos
    realizado: Decimal = ZERO  # centavos, das vendas
    proventos: int = 0  # centavos recebidos (total)
    lotes: list[Operacao] = field(default_factory=list)  # compras/posição inicial (renda fixa)

    @property
    def custo_centavos(self) -> int:
        return _inteiro(self.custo)

    @property
    def preco_medio(self) -> Decimal | None:
        """Em reais por unidade."""
        if self.quantidade <= 0:
            return None
        return self.custo / self.quantidade / 100

    @property
    def aberta(self) -> bool:
        return self.quantidade > 0

    def aplicar(self, op: Operacao) -> None:
        q = op.quantidade or ZERO
        if op.tipo in (T.POSICAO_INICIAL, T.COMPRA):
            self.quantidade += q
            self.custo += Decimal(op.valor_total_centavos)
            self.lotes.append(op)
        elif op.tipo == T.VENDA:
            if self.quantidade <= 0:
                return
            baixa = self.custo * min(q, self.quantidade) / self.quantidade
            self.realizado += Decimal(op.valor_total_centavos) - baixa
            self.custo -= baixa
            self.quantidade -= min(q, self.quantidade)
            if self.quantidade == 0:
                self.custo = ZERO
        elif op.tipo == T.DESDOBRAMENTO:
            self.quantidade *= q  # o custo não muda; o preço médio se ajusta
        elif op.tipo == T.BONIFICACAO:
            self.quantidade += q
            self.custo += Decimal(op.valor_total_centavos)
        elif op.tipo.provento:
            self.proventos += op.valor_total_centavos


def operacoes(
    sessao: Session, ate: date | None = None, ativo_id: int | None = None
) -> list[Operacao]:
    consulta = (
        select(Operacao)
        .options(joinedload(Operacao.ativo), joinedload(Operacao.conta))
        .order_by(Operacao.data, Operacao.id)
    )
    if ate:
        consulta = consulta.where(Operacao.data <= ate)
    if ativo_id:
        consulta = consulta.where(Operacao.ativo_id == ativo_id)
    return list(sessao.scalars(consulta))


def calcular(ops: Iterable[Operacao], ignorar_id: int | None = None) -> dict[int, Posicao]:
    posicoes: dict[int, Posicao] = {}
    for op in sorted(ops, key=lambda o: (o.data, o.id or 0)):
        if op.id is not None and op.id == ignorar_id:
            continue
        posicoes.setdefault(op.ativo_id, Posicao(op.ativo)).aplicar(op)
    return posicoes


def posicoes(sessao: Session, ate: date | None = None) -> dict[int, Posicao]:
    return calcular(operacoes(sessao, ate))


def quantidades_em(sessao: Session, dias: Iterable[date]) -> dict[date, dict[int, Decimal]]:
    """{dia: {ativo: quantidade}} nas datas pedidas, varrendo as operações uma vez só."""
    ordenados = sorted(set(dias))
    if not ordenados:
        return {}
    pendentes = iter(operacoes(sessao, ordenados[-1]))
    proxima = next(pendentes, None)
    posicoes_ate: dict[int, Posicao] = {}
    resultado = {}
    for dia in ordenados:
        while proxima is not None and proxima.data <= dia:
            posicoes_ate.setdefault(proxima.ativo_id, Posicao(proxima.ativo)).aplicar(proxima)
            proxima = next(pendentes, None)
        resultado[dia] = {aid: p.quantidade for aid, p in posicoes_ate.items() if p.aberta}
    return resultado


# ───────── Cotações e valor de mercado (E4-05) ─────────


def ultimas_cotacoes(sessao: Session, ate: date) -> dict[int, Cotacao]:
    ultima = (
        select(Cotacao.ativo_id, func.max(Cotacao.data).label("data"))
        .where(Cotacao.data <= ate)
        .group_by(Cotacao.ativo_id)
        .subquery()
    )
    linhas = sessao.scalars(
        select(Cotacao).join(
            ultima, (Cotacao.ativo_id == ultima.c.ativo_id) & (Cotacao.data == ultima.c.data)
        )
    )
    return {cotacao.ativo_id: cotacao for cotacao in linhas}


def _serie(sessao: Session, serie: str, desde: date, ate: date) -> list[tuple[date, Decimal]]:
    return list(
        sessao.execute(
            select(IndiceMacro.data, IndiceMacro.valor)
            .where(IndiceMacro.serie == serie, IndiceMacro.data > desde, IndiceMacro.data <= ate)
            .order_by(IndiceMacro.data)
        ).tuples()
    )


def fator_renda_fixa(sessao: Session, ativo: Ativo, desde: date, ate: date) -> Decimal | None:
    """Quanto R$ 1 aplicado em `desde` vale em `ate` (estimativa bruta, sem IR, E4-09).
    None quando faltam os índices (E5-04) para o período."""
    taxa = ativo.taxa if ativo.taxa is not None else Decimal(100)
    dias = Decimal((ate - desde).days)
    if dias <= 0:
        return Decimal(1)
    if ativo.indexador in (Indexador.CDI, Indexador.SELIC):
        valores = _serie(sessao, ativo.indexador.value, desde, ate)
        if not valores:
            return None
        fator = Decimal(1)
        for _, valor in valores:
            fator *= 1 + valor / 100 * taxa / 100
        return fator
    if ativo.indexador == Indexador.IPCA:
        valores = _serie(sessao, "ipca", desde, ate)
        if not valores:
            return None
        fator = Decimal(1)
        for _, valor in valores:
            fator *= 1 + valor / 100
        return fator * (1 + taxa / 100) ** (dias / 365)
    if ativo.indexador == Indexador.PREFIXADO:
        return (1 + taxa / 100) ** (dias / 365)
    return None


def estimar_renda_fixa(sessao: Session, posicao: Posicao, ate: date) -> int | None:
    """Soma de cada aplicação corrigida pelo indexador, proporcional ao que sobrou depois
    de resgates (vendas)."""
    if not posicao.lotes or not posicao.aberta:
        return None
    aplicado = sum(Decimal(lote.valor_total_centavos) for lote in posicao.lotes)
    corrigido = ZERO
    for lote in posicao.lotes:
        fator = fator_renda_fixa(sessao, posicao.ativo, lote.data, ate)
        if fator is None:
            return None
        corrigido += Decimal(lote.valor_total_centavos) * fator
    return _inteiro(corrigido * posicao.custo / aplicado) if aplicado else None


@dataclass
class LinhaCarteira:
    posicao: Posicao
    valor: int
    origem: str  # "cotacao", "estimativa" ou "custo"
    cotacao: Cotacao | None = None
    proventos_12m: int = 0
    participacao: Decimal = ZERO  # % do patrimônio

    @property
    def ativo(self) -> Ativo:
        return self.posicao.ativo

    @property
    def resultado(self) -> int:
        return self.valor - self.posicao.custo_centavos

    @property
    def resultado_pct(self) -> Decimal | None:
        custo = self.posicao.custo_centavos
        return Decimal(self.resultado * 100) / custo if custo else None

    @property
    def yield_on_cost(self) -> Decimal | None:
        """Proventos dos últimos 12 meses ÷ custo, em %."""
        custo = self.posicao.custo_centavos
        return Decimal(self.proventos_12m * 100) / custo if custo else None


def preco_do_tesouro_serve(sessao: Session, posicao: Posicao) -> bool:
    """O preço do Tesouro é por título. Serve se a posição foi lançada em nº de títulos: o preço
    médio fica perto do preço da época. Quem lançou "1 × R$ 5.000" segue pela estimativa."""
    if not posicao.lotes or not posicao.aberta:
        return False
    primeiro = posicao.lotes[0]
    preco = sessao.scalar(
        select(Cotacao.fechamento)
        .where(
            Cotacao.ativo_id == posicao.ativo.id, Cotacao.data <= primeiro.data + timedelta(days=7)
        )
        .order_by(Cotacao.data.desc())
        .limit(1)
    )
    pago = Decimal(primeiro.valor_total_centavos) / 100 / (primeiro.quantidade or 1)
    return bool(preco) and abs(pago / preco - 1) <= MARGEM_PRECO_TESOURO


def valor_da_posicao(
    sessao: Session, posicao: Posicao, cotacao: Cotacao | None, ate: date
) -> tuple[int, str]:
    if cotacao is not None and (
        posicao.ativo.classe.tem_cotacao
        or cotacao.fonte == "manual"
        or (cotacao.fonte == "tesouro" and preco_do_tesouro_serve(sessao, posicao))
    ):
        return _inteiro(posicao.quantidade * cotacao.fechamento * 100), "cotacao"
    if posicao.ativo.classe.renda_fixa:
        estimado = estimar_renda_fixa(sessao, posicao, ate)
        if estimado is not None:
            return estimado, "estimativa"
    return posicao.custo_centavos, "custo"


def proventos_por_ativo(sessao: Session, inicio: date, fim: date) -> dict[int, int]:
    linhas = sessao.execute(
        select(Operacao.ativo_id, func.sum(Operacao.valor_total_centavos))
        .where(
            Operacao.tipo.in_([T.DIVIDENDO, T.JCP, T.RENDIMENTO]),
            Operacao.data >= inicio,
            Operacao.data <= fim,
        )
        .group_by(Operacao.ativo_id)
    )
    return dict(list(linhas.tuples()))


@dataclass
class Carteira:
    linhas: list[LinhaCarteira]
    realizado: int
    proventos_12m: int

    @property
    def patrimonio(self) -> int:
        return sum(linha.valor for linha in self.linhas)

    @property
    def aportado(self) -> int:
        return sum(linha.posicao.custo_centavos for linha in self.linhas)

    @property
    def resultado(self) -> int:
        return self.patrimonio - self.aportado

    @property
    def resultado_pct(self) -> Decimal | None:
        return Decimal(self.resultado * 100) / self.aportado if self.aportado else None

    @property
    def yield_on_cost(self) -> Decimal | None:
        return Decimal(self.proventos_12m * 100) / self.aportado if self.aportado else None

    @property
    def sem_cotacao(self) -> list[LinhaCarteira]:
        return [linha for linha in self.linhas if linha.origem == "custo"]


ORDENS = {
    "valor": lambda ln: -ln.valor,
    "ativo": lambda ln: ln.ativo.ticker,
    "resultado": lambda ln: -(ln.resultado_pct or Decimal(-(10**9))),
    "proventos": lambda ln: -ln.proventos_12m,
    "classe": lambda ln: (ln.ativo.classe.rotulo, -ln.valor),
}


def montar(sessao: Session, hoje: date, ordem: str = "valor") -> Carteira:
    todas = posicoes(sessao, hoje)
    cotacoes = ultimas_cotacoes(sessao, hoje)
    proventos = proventos_por_ativo(sessao, hoje - timedelta(days=365), hoje)
    linhas = []
    for ativo_id, posicao in todas.items():
        if not posicao.aberta:
            continue
        cotacao = cotacoes.get(ativo_id)
        valor, origem = valor_da_posicao(sessao, posicao, cotacao, hoje)
        linhas.append(LinhaCarteira(posicao, valor, origem, cotacao, proventos.get(ativo_id, 0)))
    total = sum(linha.valor for linha in linhas)
    for linha in linhas:
        linha.participacao = Decimal(linha.valor * 100) / total if total else ZERO
    linhas.sort(key=ORDENS.get(ordem, ORDENS["valor"]))
    realizado = _inteiro(sum((p.realizado for p in todas.values()), ZERO))
    return Carteira(linhas, realizado, sum(proventos.values()))


# ───────── Gráficos (E4-08) ─────────


@dataclass(frozen=True)
class FatiaClasse:
    nome: str
    slot: int
    valor: int
    percentual: Decimal


def alocacao(carteira: Carteira) -> list[FatiaClasse]:
    """Valor por classe: as 5 maiores e o resto em "Outras"."""
    por_classe: dict[ClasseAtivo, int] = {}
    for linha in carteira.linhas:
        por_classe[linha.ativo.classe] = por_classe.get(linha.ativo.classe, 0) + linha.valor
    total = sum(por_classe.values())
    if not total:
        return []
    ordenadas = sorted(por_classe.items(), key=lambda item: -item[1])
    visiveis = ordenadas if len(ordenadas) <= MAX_CLASSES + 1 else ordenadas[:MAX_CLASSES]
    fatias = [
        FatiaClasse(classe.rotulo, classe.cor_slot, valor, Decimal(valor * 100) / total)
        for classe, valor in visiveis
    ]
    resto = sum(valor for _, valor in ordenadas[len(visiveis) :])
    if resto:
        fatias.append(FatiaClasse("Outras", 0, resto, Decimal(resto * 100) / total))
    return fatias


def evolucao(sessao: Session, ate: Competencia, hoje: date, quantidade: int = 12) -> list[tuple]:
    """[(mês, patrimônio, aportado)] no fim de cada mês (no mês atual, hoje), desde a
    primeira operação. Sem cotação na data, vale o custo."""
    primeira = sessao.scalar(select(func.min(Operacao.data)))
    if primeira is None:
        return []
    # Uma passada só pelas operações: a posição de cada mês continua a do mês anterior.
    pendentes = iter(operacoes(sessao, hoje))
    proxima = next(pendentes, None)
    posicoes_ate: dict[int, Posicao] = {}
    resultado = []
    for mes in meses_ate(ate, quantidade):
        if mes.primeiro_dia > hoje:
            continue
        dia = dia_de_referencia(mes, hoje)
        while proxima is not None and proxima.data <= dia:
            posicoes_ate.setdefault(proxima.ativo_id, Posicao(proxima.ativo)).aplicar(proxima)
            proxima = next(pendentes, None)
        if mes.ultimo_dia < primeira:
            continue
        cotacoes = ultimas_cotacoes(sessao, dia)
        patrimonio = aportado = 0
        for ativo_id, posicao in posicoes_ate.items():
            if posicao.aberta:
                valor, _ = valor_da_posicao(sessao, posicao, cotacoes.get(ativo_id), dia)
                patrimonio += valor
                aportado += posicao.custo_centavos
        resultado.append((mes, patrimonio, aportado))
    return resultado

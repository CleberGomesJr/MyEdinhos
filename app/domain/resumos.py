"""Resumos para os dashboards (E3).

Regras (vault "E3 - Dashboards e Resumos"):
- Entradas, Saídas e Resultado são por **competência**: contam todos os lançamentos do mês
  (pagos ou não), incluindo compras no cartão; transferências e pagamentos de fatura ficam fora.
- Saldo é **caixa**: só lançamentos pagos nas contas (`contas.saldos`).
- Visão **caixa** (E3-14), opcional nos resumos: o que entrou e saiu das contas pela data,
  só o que está pago. Compras no cartão não entram; o pagamento da fatura é que vira saída
  (sem categoria: aparece como "Faturas de cartão").
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, aliased, joinedload

from app.db.models import Cartao, Categoria, Conta, Lancamento, TipoLancamento
from app.domain import cartoes, contas
from app.domain.competencia import Competencia

MAX_CATEGORIAS = 7  # mais que isso vira "Outros" (vault "04 - Design System")


# ───────── E3-01: resultado do mês e saldos ─────────


@dataclass(frozen=True)
class Resultado:
    entradas: int = 0
    saidas: int = 0

    @property
    def saldo(self) -> int:
        return self.entradas - self.saidas

    @property
    def taxa_poupanca(self) -> Decimal | None:
        """Quanto das entradas sobrou, em pontos percentuais."""
        if self.entradas <= 0:
            return None
        return Decimal(self.saldo * 100) / Decimal(self.entradas)


def _somas_por_mes(sessao: Session, competencias: list[str]) -> dict[str, Resultado]:
    valor = Lancamento.valor_centavos
    linhas = sessao.execute(
        select(
            Lancamento.competencia,
            func.sum(case((Lancamento.tipo == TipoLancamento.ENTRADA, valor), else_=0)),
            func.sum(case((Lancamento.tipo == TipoLancamento.SAIDA, valor), else_=0)),
        )
        .where(Lancamento.competencia.in_(competencias))
        .group_by(Lancamento.competencia)
    )
    return {competencia: Resultado(entradas, saidas) for competencia, entradas, saidas in linhas}


_MES_DA_DATA = func.strftime("%Y-%m", Lancamento.data)


def _filtro_caixa(meses: list[Competencia]):
    """Lançamentos pagos que movem uma conta, dentro dos meses (pela data).

    Parcelas retroativas ficam de fora: já foram pagas antes e o saldo informado as considera.
    """
    return (
        Lancamento.pago,
        ~Lancamento.retroativo,
        Lancamento.conta_id.is_not(None),
        Lancamento.data >= min(meses).primeiro_dia,
        Lancamento.data <= max(meses).ultimo_dia,
    )


def _somas_caixa(sessao: Session, meses: list[Competencia]) -> dict[str, Resultado]:
    valor = Lancamento.valor_centavos
    saida = (Lancamento.tipo == TipoLancamento.SAIDA) | (
        (Lancamento.tipo == TipoLancamento.TRANSFERENCIA) & Lancamento.cartao_id.is_not(None)
    )
    linhas = sessao.execute(
        select(
            _MES_DA_DATA,
            func.sum(case((Lancamento.tipo == TipoLancamento.ENTRADA, valor), else_=0)),
            func.sum(case((saida, valor), else_=0)),
        )
        .where(*_filtro_caixa(meses))
        .group_by(_MES_DA_DATA)
    )
    return {mes: Resultado(entradas, saidas) for mes, entradas, saidas in linhas}


def _somas(sessao: Session, meses: list[Competencia], caixa: bool) -> dict[str, Resultado]:
    if caixa:
        return _somas_caixa(sessao, meses)
    return _somas_por_mes(sessao, [str(mes) for mes in meses])


def resultado(sessao: Session, mes: Competencia, caixa: bool = False) -> Resultado:
    return _somas(sessao, [mes], caixa).get(str(mes), Resultado())


def meses_ate(mes: Competencia, quantidade: int) -> list[Competencia]:
    """Os `quantidade` meses terminando em `mes`, do mais antigo ao mais recente."""
    meses = [mes]
    while len(meses) < quantidade:
        meses.insert(0, meses[0].anterior())
    return meses


def fluxo(
    sessao: Session, ate: Competencia, quantidade: int = 12, caixa: bool = False
) -> list[tuple]:
    """[(mês, Resultado)] dos últimos `quantidade` meses até `ate`."""
    meses = meses_ate(ate, quantidade)
    somas = _somas(sessao, meses, caixa)
    return [(mes, somas.get(str(mes), Resultado())) for mes in meses]


def _soma_das_ativas(sessao: Session, saldos: dict[int, int]) -> int:
    ativas = {conta.id for conta in contas.listar(sessao)}
    return sum(valor for conta_id, valor in saldos.items() if conta_id in ativas)


def saldo_total(sessao: Session, dia: date) -> int:
    """Soma do saldo (caixa) das contas ativas ao fim do dia."""
    return _soma_das_ativas(sessao, contas.saldos(sessao, dia))


def saldo_previsto(sessao: Session, mes: Competencia, hoje: date) -> int:
    """Como em Contas: pagos + pendentes até o fim do mês, menos as faturas que vencem até lá."""
    previstos = contas.saldos(sessao, mes.ultimo_dia, incluir_pendentes=True)
    return _soma_das_ativas(sessao, previstos) - faturas_a_pagar(sessao, mes.ultimo_dia, hoje)


def variacao(atual: int, anterior: int) -> Decimal | None:
    """Variação percentual; sem base de comparação (anterior = 0) não há variação."""
    if anterior == 0:
        return None
    return Decimal((atual - anterior) * 100) / Decimal(abs(anterior))


def dia_de_referencia(mes: Competencia, hoje: date) -> date:
    """Hoje, no mês atual; o último dia, nos meses passados; o primeiro, nos futuros."""
    return min(max(hoje, mes.primeiro_dia), mes.ultimo_dia)


# ───────── E3-04: despesas por categoria ─────────


@dataclass(frozen=True)
class FatiaCategoria:
    nome: str
    slot: int  # 0 = cinza ("Outros")
    valor: int
    percentual: Decimal
    categoria_id: int | None = None  # None na fatia "Outros"


def saidas_por_principal(sessao: Session, mes: Competencia) -> list[tuple[int, int]]:
    """[(id da categoria principal, total)] das saídas do mês, do maior para o menor.
    Subcategorias somam na mãe."""
    pai = aliased(Categoria)
    principal = func.coalesce(pai.id, Categoria.id)
    return [
        (categoria_id, valor)
        for categoria_id, valor in sessao.execute(
            select(principal, func.sum(Lancamento.valor_centavos))
            .join(Categoria, Categoria.id == Lancamento.categoria_id)
            .outerjoin(pai, pai.id == Categoria.pai_id)
            .where(Lancamento.competencia == str(mes), Lancamento.tipo == TipoLancamento.SAIDA)
            .group_by(principal)
            .order_by(func.sum(Lancamento.valor_centavos).desc(), principal)
        )
    ]


def _saidas_caixa_por_principal(sessao: Session, mes: Competencia) -> list[tuple[int | None, int]]:
    """Como `saidas_por_principal`, mas pelo caixa; pagamentos de fatura ficam com id None."""
    pai = aliased(Categoria)
    principal = func.coalesce(pai.id, Categoria.id)
    linhas = sessao.execute(
        select(principal, func.sum(Lancamento.valor_centavos))
        .outerjoin(Categoria, Categoria.id == Lancamento.categoria_id)
        .outerjoin(pai, pai.id == Categoria.pai_id)
        .where(
            *_filtro_caixa([mes]),
            (Lancamento.tipo == TipoLancamento.SAIDA)
            | (
                (Lancamento.tipo == TipoLancamento.TRANSFERENCIA)
                & Lancamento.cartao_id.is_not(None)
            ),
        )
        .group_by(principal)
    )
    return sorted(linhas, key=lambda linha: -linha[1])


FATURAS = "Faturas de cartão"


def despesas_por_categoria(
    sessao: Session, mes: Competencia, caixa: bool = False
) -> list[FatiaCategoria]:
    """Saídas do mês por categoria principal: as 7 maiores e o resto em "Outros".
    No caixa, os pagamentos de fatura aparecem juntos como "Faturas de cartão"."""
    linhas = (
        _saidas_caixa_por_principal(sessao, mes) if caixa else saidas_por_principal(sessao, mes)
    )
    total = sum(valor for _, valor in linhas)
    if not total:
        return []
    ids = [categoria_id for categoria_id, _ in linhas]
    nomes = {c.id: c for c in sessao.scalars(select(Categoria).where(Categoria.id.in_(ids)))}

    def fatia(nome, slot, valor, categoria_id=None):
        return FatiaCategoria(nome, slot, valor, Decimal(valor * 100) / total, categoria_id)

    # Com até 8 categorias, todas aparecem; acima disso, as 7 maiores + "Outros".
    visiveis = linhas if len(linhas) <= MAX_CATEGORIAS + 1 else linhas[:MAX_CATEGORIAS]
    fatias = [
        fatia(nomes[categoria_id].nome, nomes[categoria_id].cor_slot, valor, categoria_id)
        if categoria_id is not None
        else fatia(FATURAS, 0, valor)
        for categoria_id, valor in visiveis
    ]
    resto = sum(valor for _, valor in linhas[len(visiveis) :])
    if resto:
        fatias.append(fatia("Outros", 0, resto))
    return fatias


# ───────── E3-05: evolução do saldo ─────────


def saldo_diario(sessao: Session, mes: Competencia, hoje: date) -> list[tuple[date, int]]:
    """Saldo total ao fim de cada dia do mês, até hoje (mês futuro: vazio)."""
    if mes.primeiro_dia > hoje:
        return []
    ultimo = min(mes.ultimo_dia, hoje)
    dias = (ultimo - mes.primeiro_dia).days + 1
    return [
        (dia, saldo_total(sessao, dia))
        for dia in (mes.primeiro_dia + timedelta(days=i) for i in range(dias))
    ]


def saldo_mensal(
    sessao: Session, ate: Competencia, hoje: date, quantidade: int = 12
) -> list[tuple[Competencia, int]]:
    """Saldo total no fim de cada mês (no mês atual, hoje). Ficam de fora os meses futuros e
    os anteriores ao primeiro saldo inicial (mostrariam um zero que não aconteceu)."""
    inicio = min((conta.data_saldo_inicial for conta in contas.listar(sessao)), default=None)
    if inicio is None:
        return []
    return [
        (mes, saldo_total(sessao, dia_de_referencia(mes, hoje)))
        for mes in meses_ate(ate, quantidade)
        if mes.primeiro_dia <= hoje and mes.ultimo_dia >= inicio
    ]


# ───────── E3-06: saldos por conta ─────────


@dataclass(frozen=True)
class SaldoConta:
    conta: Conta
    saldo: int
    proporcao: Decimal  # 0–100, em relação ao maior saldo (em módulo)


def saldos_por_conta(sessao: Session, dia: date) -> list[SaldoConta]:
    saldos = contas.saldos(sessao, dia)
    itens = [(conta, saldos.get(conta.id, 0)) for conta in contas.listar(sessao)]
    maior = max((abs(saldo) for _, saldo in itens), default=0)
    return sorted(
        (
            SaldoConta(conta, saldo, Decimal(abs(saldo) * 100) / maior if maior else Decimal(0))
            for conta, saldo in itens
        ),
        key=lambda item: -item.saldo,
    )


# ───────── E3-08: últimos lançamentos e a pagar ─────────


def ultimos(sessao: Session, hoje: date, quantidade: int = 6) -> list[Lancamento]:
    return list(
        sessao.scalars(
            select(Lancamento)
            .options(
                joinedload(Lancamento.conta),
                joinedload(Lancamento.cartao),
                joinedload(Lancamento.categoria).joinedload(Categoria.pai),
            )
            .where(Lancamento.data <= hoje)
            .order_by(Lancamento.data.desc(), Lancamento.id.desc())
            .limit(quantidade)
        )
    )


@dataclass(frozen=True)
class AVencer:
    descricao: str
    vencimento: date
    valor: int
    atrasado: bool
    lancamento: Lancamento | None = None  # None para fatura de cartão
    fatura_id: int | None = None


def a_pagar(sessao: Session, hoje: date, dias: int = 7) -> list[AVencer]:
    """Pendentes (saídas) que vencem até hoje + `dias`, os atrasados e as faturas a vencer."""
    limite = hoje + timedelta(days=dias)
    vencimento = func.coalesce(Lancamento.vencimento, Lancamento.data)
    pendentes = sessao.scalars(
        select(Lancamento)
        .where(~Lancamento.pago, Lancamento.tipo == TipoLancamento.SAIDA, vencimento <= limite)
        .order_by(vencimento, Lancamento.id)
    )
    itens = [
        AVencer(
            item.descricao,
            item.vencimento or item.data,
            item.valor_centavos,
            (item.vencimento or item.data) < hoje,
            lancamento=item,
        )
        for item in pendentes
    ]
    for cartao in sessao.scalars(select(Cartao).where(Cartao.ativo)):
        for resumo in cartoes.resumir(sessao, cartoes.faturas_do_cartao(sessao, cartao), hoje):
            fatura = resumo.fatura
            fechada = fatura.data_fechamento < hoje
            if resumo.restante and fechada and fatura.data_vencimento <= limite:
                itens.append(
                    AVencer(
                        f"Fatura {cartao.nome}",
                        fatura.data_vencimento,
                        resumo.restante,
                        fatura.data_vencimento < hoje,
                        fatura_id=fatura.id,
                    )
                )
    return sorted(itens, key=lambda item: item.vencimento)


def faturas_a_pagar(sessao: Session, ate: date, hoje: date) -> int:
    """Total que falta pagar das faturas que vencem até `ate` (linha separada do saldo)."""
    return sum(cartoes.a_pagar_por_conta(sessao, ate, hoje).values())


# ───────── E3-13: cartões na visão geral ─────────


@dataclass(frozen=True)
class UsoCartao:
    cartao: Cartao
    fatura: cartoes.Resumo
    limite: cartoes.Limite

    @property
    def usado(self) -> int:
        return self.limite.limite - self.limite.disponivel

    @property
    def percentual(self) -> Decimal:
        if self.limite.limite <= 0:
            return Decimal(0)
        return Decimal(self.usado * 100) / Decimal(self.limite.limite)

    @property
    def estado(self) -> str:
        """Uso do limite: ok até 70%, atenção até 90%, crítico acima."""
        if self.percentual > 90:
            return "critico"
        return "atencao" if self.percentual > 70 else "ok"


def uso_dos_cartoes(sessao: Session, hoje: date) -> list[UsoCartao]:
    return [
        UsoCartao(
            cartao,
            cartoes.resumo(sessao, cartoes.fatura_atual(sessao, cartao, hoje), hoje),
            cartoes.limite(sessao, cartao, hoje),
        )
        for cartao in cartoes.listar(sessao)
    ]

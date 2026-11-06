"""Objetivos (E6-09): reserva de emergência, renda passiva e metas de patrimônio com prazo.

Projeções são simples e mostram as premissas (retorno ao ano, yield, aporte): servem para
ter ordem de grandeza, não são promessa de rentabilidade.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import GrupoAlocacao, Objetivo
from app.domain import alocacao, carteira, preferencias, proventos
from app.domain.ativos import ler_decimal
from app.domain.desfazer import excluir_com_desfazer
from app.domain.formularios import ErroValidacao, Leitor

UM = Decimal(1)
MAX_MESES = 600  # 50 anos: além disso, "não chega"
PREMISSAS = {
    "premissa_retorno_aa": Decimal(10),  # % ao ano, nominal, sem IR
    "premissa_yield": Decimal(7),  # % ao ano em proventos sobre o investido em renda variável
}
APORTE = "aporte_mensal_planejado"  # centavos


@dataclass(frozen=True)
class Premissas:
    retorno_aa: Decimal
    yield_aa: Decimal
    aporte_mensal: int

    @property
    def taxa_mensal(self) -> Decimal:
        return (UM + self.retorno_aa / 100) ** (UM / 12) - 1


def premissas(sessao: Session) -> Premissas:
    def decimal(chave):
        bruto = preferencias.obter(sessao, chave)
        return Decimal(bruto) if bruto else PREMISSAS[chave]

    return Premissas(
        decimal("premissa_retorno_aa"),
        decimal("premissa_yield"),
        int(preferencias.obter(sessao, APORTE) or 0),
    )


def salvar_premissas(sessao: Session, dados: Mapping[str, str]) -> None:
    leitor = Leitor(dados)
    retorno = ler_decimal(leitor, "premissa_retorno_aa", positivo=False)
    rendimento = ler_decimal(leitor, "premissa_yield")
    aporte = leitor.centavos("aporte_mensal", positivo=False, obrigatorio=False)
    meses = leitor.inteiro("reserva_meses", 1, 24)
    if retorno is not None and not -20 <= retorno <= 50:
        leitor.erros["premissa_retorno_aa"] = "Use de −20 a 50."
    if rendimento is not None and rendimento > 30:
        leitor.erros["premissa_yield"] = "Use até 30."
    if aporte is not None and aporte < 0:
        leitor.erros["aporte_mensal"] = "O aporte não pode ser negativo."
    leitor.verificar()
    preferencias.definir(sessao, "premissa_retorno_aa", format(retorno, "f"))
    preferencias.definir(sessao, "premissa_yield", format(rendimento, "f"))
    preferencias.definir(sessao, APORTE, str(aporte or 0))
    alocacao.definir_meses_reserva(sessao, meses)


def meses_para(atual: int, alvo: int, aporte: int, taxa_mensal: Decimal) -> int | None:
    """Meses até `atual` crescer (juros + aporte no fim de cada mês) até `alvo`."""
    if atual >= alvo:
        return 0
    saldo = Decimal(atual)
    for mes in range(1, MAX_MESES + 1):
        saldo = saldo * (UM + taxa_mensal) + aporte
        if saldo >= alvo:
            return mes
    return None


def aporte_para(atual: int, alvo: int, meses: int, taxa_mensal: Decimal) -> int:
    """Aporte mensal que leva `atual` a `alvo` em `meses` (0 se já chega sem aportar)."""
    if meses <= 0:
        return max(alvo - atual, 0)
    crescimento = (UM + taxa_mensal) ** meses
    falta = Decimal(alvo) - Decimal(atual) * crescimento
    if falta <= 0:
        return 0
    fator = (crescimento - 1) / taxa_mensal if taxa_mensal else Decimal(meses)
    return int((falta / fator).to_integral_value(rounding="ROUND_CEILING"))


def _meses_ate(hoje: date, prazo: date) -> int:
    return (prazo.year - hoje.year) * 12 + prazo.month - hoje.month


@dataclass(frozen=True)
class Progresso:
    nome: str
    alvo: int
    atual: int
    prazo: date | None
    aporte: int
    meses_estimados: int | None  # com o aporte e a premissa de retorno
    aporte_para_prazo: int | None
    objetivo: Objetivo | None = None

    @property
    def percentual(self) -> Decimal:
        return min(Decimal(self.atual * 100) / self.alvo, Decimal(100)) if self.alvo else Decimal(0)

    @property
    def no_prazo(self) -> bool | None:
        if self.prazo is None or self.meses_estimados is None or self.aporte_para_prazo is None:
            return None
        return self.aporte >= self.aporte_para_prazo


def listar(sessao: Session) -> list[Objetivo]:
    return list(
        sessao.scalars(
            select(Objetivo).where(Objetivo.ativo).order_by(Objetivo.prazo, Objetivo.nome)
        )
    )


def progresso(sessao: Session, hoje: date, cart: carteira.Carteira, base: int) -> list[Progresso]:
    """Objetivos de patrimônio: `base` = patrimônio considerado (carteira + reserva)."""
    p = premissas(sessao)
    itens = []
    for objetivo in listar(sessao):
        aporte = objetivo.aporte_mensal_centavos or p.aporte_mensal
        meses_prazo = _meses_ate(hoje, objetivo.prazo) if objetivo.prazo else None
        itens.append(
            Progresso(
                objetivo.nome, objetivo.valor_alvo_centavos, base, objetivo.prazo, aporte,
                meses_para(base, objetivo.valor_alvo_centavos, aporte, p.taxa_mensal),
                aporte_para(base, objetivo.valor_alvo_centavos, meses_prazo, p.taxa_mensal)
                if meses_prazo is not None else None,
                objetivo,
            )
        )  # fmt: skip
    return itens


@dataclass(frozen=True)
class RendaPassiva:
    meta_mensal: int
    media_12m: int
    parcela_rv: Decimal  # % da carteira em renda variável (alvo), p/ o patrimônio total
    patrimonio_rv_necessario: int
    patrimonio_total_necessario: int
    patrimonio_atual: int
    meses_estimados: int | None


def renda_passiva(
    sessao: Session, hoje: date, cart: carteira.Carteira, base: int
) -> RendaPassiva | None:
    """Patrimônio para a meta de proventos: meta × 12 ÷ yield (premissa), dividido pela
    parcela de renda variável da alocação-alvo."""
    meta = proventos.meta(sessao, cart.proventos_12m, cart.yield_on_cost)
    if meta is None:
        return None
    p = premissas(sessao)
    rv = (GrupoAlocacao.ACOES, GrupoAlocacao.FIIS, GrupoAlocacao.EXTERIOR)
    alvos = alocacao.metas(sessao)
    parcela = sum((alvos[g].percentual_alvo for g in rv if g in alvos), Decimal(0)) or Decimal(100)
    necessario_rv = int(Decimal(meta.mensal * 12 * 100) / p.yield_aa)
    necessario = int(necessario_rv * 100 / parcela)
    return RendaPassiva(
        meta.mensal, meta.media_12m, parcela, necessario_rv, necessario, base,
        meses_para(base, necessario, p.aporte_mensal, p.taxa_mensal),
    )  # fmt: skip


def salvar(sessao: Session, dados: Mapping[str, str], objetivo: Objetivo | None = None) -> Objetivo:
    leitor = Leitor(dados)
    nome = leitor.texto("nome", maximo=80)
    alvo = leitor.centavos("valor_alvo")
    prazo = leitor.data("prazo", obrigatorio=False)
    aporte = leitor.centavos("aporte_mensal", positivo=False, obrigatorio=False)
    descricao = leitor.texto("descricao", maximo=500, obrigatorio=False)
    if aporte is not None and aporte < 0:
        leitor.erros["aporte_mensal"] = "O aporte não pode ser negativo."
    if leitor.erros:
        raise ErroValidacao(leitor.erros)
    if objetivo is None:
        objetivo = Objetivo()
        sessao.add(objetivo)
    objetivo.nome, objetivo.valor_alvo_centavos, objetivo.prazo = nome, alvo, prazo
    objetivo.aporte_mensal_centavos, objetivo.descricao = aporte or 0, descricao
    sessao.flush()
    return objetivo


def excluir(sessao: Session, objetivo: Objetivo):
    return excluir_com_desfazer(sessao, [objetivo], f"Objetivo “{objetivo.nome}”")

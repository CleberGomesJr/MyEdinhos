"""Cartões de crédito e faturas.

Regra de contabilização (vault "03 - Modelo de Dados"):
- a compra é despesa no mês em que aconteceu (competência);
- o saldo da conta só cai quando a fatura é paga, por uma transferência conta → cartão,
  que não conta como despesa (sem dupla contagem).

A fatura é identificada pelo mês de vencimento (`referencia`). Com fechamento no dia 19 e
vencimento no 26, compras de 20/09 a 19/10 vão para a fatura que vence em 26/10.
"""

import calendar
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, joinedload

from app.db.models import (
    Cartao,
    Categoria,
    CompraNoFechamento,
    Conta,
    Fatura,
    Lancamento,
    TipoLancamento,
)
from app.domain.competencia import Competencia
from app.domain.formularios import Leitor


def _dia(mes: Competencia, dia: int) -> date:
    """Dia `dia` do mês; em mês mais curto, o último dia (fechamento 31 → 30/04)."""
    return date(mes.ano, mes.mes, min(dia, calendar.monthrange(mes.ano, mes.mes)[1]))


def _vence_no_mes_seguinte(cartao: Cartao) -> bool:
    return cartao.dia_vencimento <= cartao.dia_fechamento


def referencia_da_compra(cartao: Cartao, dia: date) -> Competencia:
    """Mês de vencimento da fatura em que uma compra feita em `dia` entra."""
    mes = Competencia.de_data(dia)
    fechamento = _dia(mes, cartao.dia_fechamento)
    fecha_neste_mes = dia < fechamento or (
        dia == fechamento and cartao.compra_no_fechamento == CompraNoFechamento.FATURA_ATUAL
    )
    mes_fechamento = mes if fecha_neste_mes else mes.proxima()
    return mes_fechamento.proxima() if _vence_no_mes_seguinte(cartao) else mes_fechamento


def datas_da_fatura(cartao: Cartao, referencia: Competencia) -> tuple[date, date]:
    """(fechamento, vencimento) da fatura que vence em `referencia`."""
    mes_fechamento = referencia.anterior() if _vence_no_mes_seguinte(cartao) else referencia
    return _dia(mes_fechamento, cartao.dia_fechamento), _dia(referencia, cartao.dia_vencimento)


def obter_fatura(sessao: Session, cartao: Cartao, referencia: Competencia) -> Fatura:
    fatura = sessao.scalar(
        select(Fatura).where(Fatura.cartao_id == cartao.id, Fatura.referencia == str(referencia))
    )
    if fatura is None:
        fechamento, vencimento = datas_da_fatura(cartao, referencia)
        fatura = Fatura(
            cartao=cartao,
            referencia=str(referencia),
            data_fechamento=fechamento,
            data_vencimento=vencimento,
        )
        sessao.add(fatura)
        sessao.flush()
    return fatura


def fatura_da_compra(sessao: Session, cartao: Cartao, dia: date) -> Fatura:
    return obter_fatura(sessao, cartao, referencia_da_compra(cartao, dia))


def fatura_atual(sessao: Session, cartao: Cartao, hoje: date) -> Fatura:
    """A fatura aberta hoje (a que recebe uma compra feita hoje)."""
    return fatura_da_compra(sessao, cartao, hoje)


# ───────── Situação da fatura ─────────


class Situacao(StrEnum):
    ABERTA = "aberta"
    FECHADA = "fechada"
    PAGA = "paga"
    VENCIDA = "vencida"

    @property
    def rotulo(self) -> str:
        return {"aberta": "Aberta", "fechada": "Fechada", "paga": "Paga", "vencida": "Vencida"}[
            self.value
        ]


@dataclass
class Resumo:
    fatura: Fatura
    compras: int  # saídas no cartão
    creditos: int  # estornos/créditos no cartão (reduzem a fatura)
    # pagamentos (transferências conta → cartão) + parcelas retroativas, já quitadas antes
    pago: int
    situacao: Situacao

    @property
    def total(self) -> int:
        return self.compras - self.creditos

    @property
    def restante(self) -> int:
        return max(self.total - self.pago, 0)


def _somas(sessao: Session, faturas: list[Fatura]) -> dict[int, tuple[int, int, int]]:
    """{fatura_id: (compras, créditos, pagos)} em uma consulta.

    "Pagos" soma os pagamentos da fatura (transferências) e as compras retroativas: parcelas
    que já foram pagas antes de serem lançadas, sem passar por nenhuma conta daqui.
    """
    if not faturas:
        return {}
    valor = Lancamento.valor_centavos

    def soma(condicao):
        return func.coalesce(func.sum(case((condicao, valor), else_=0)), 0)

    linhas = sessao.execute(
        select(
            Lancamento.fatura_id,
            soma(Lancamento.tipo == TipoLancamento.SAIDA),
            soma(Lancamento.tipo == TipoLancamento.ENTRADA),
            soma(Lancamento.tipo == TipoLancamento.TRANSFERENCIA),
            soma((Lancamento.tipo == TipoLancamento.SAIDA) & Lancamento.retroativo),
        )
        .where(Lancamento.fatura_id.in_([fatura.id for fatura in faturas]))
        .group_by(Lancamento.fatura_id)
    )
    return {
        fatura_id: (compras, creditos, pagamentos + retroativas)
        for fatura_id, compras, creditos, pagamentos, retroativas in linhas
    }


def _situacao(fatura: Fatura, total: int, pago: int, hoje: date) -> Situacao:
    if hoje <= fatura.data_fechamento:
        return Situacao.ABERTA
    if pago >= total:
        return Situacao.PAGA
    return Situacao.VENCIDA if hoje > fatura.data_vencimento else Situacao.FECHADA


def resumir(sessao: Session, faturas: list[Fatura], hoje: date) -> list[Resumo]:
    somas = _somas(sessao, faturas)
    resumos = []
    for fatura in faturas:
        compras, creditos, pago = somas.get(fatura.id, (0, 0, 0))
        situacao = _situacao(fatura, compras - creditos, pago, hoje)
        resumos.append(Resumo(fatura, compras, creditos, pago, situacao))
    return resumos


def resumo(sessao: Session, fatura: Fatura, hoje: date) -> Resumo:
    return resumir(sessao, [fatura], hoje)[0]


def faturas_do_cartao(sessao: Session, cartao: Cartao) -> list[Fatura]:
    return list(
        sessao.scalars(
            select(Fatura).where(Fatura.cartao_id == cartao.id).order_by(Fatura.referencia)
        )
    )


@dataclass
class Limite:
    limite: int
    faturas_abertas: int  # fatura atual + fechadas ainda não pagas
    parcelas_futuras: int  # faturas que ainda vão abrir (parcelas já lançadas)

    @property
    def disponivel(self) -> int:
        return self.limite - self.faturas_abertas - self.parcelas_futuras


def limite(sessao: Session, cartao: Cartao, hoje: date) -> Limite:
    atual = fatura_atual(sessao, cartao, hoje)
    abertas = futuras = 0
    for item in resumir(sessao, faturas_do_cartao(sessao, cartao), hoje):
        if item.fatura.referencia > atual.referencia:
            futuras += item.restante
        else:
            abertas += item.restante
    return Limite(cartao.limite_centavos, abertas, futuras)


def a_pagar_por_conta(sessao: Session, ate: date, hoje: date) -> dict[int, int]:
    """Quanto falta pagar das faturas que vencem até `ate`, por conta pagadora."""
    faturas = list(
        sessao.scalars(
            select(Fatura).options(joinedload(Fatura.cartao)).where(Fatura.data_vencimento <= ate)
        )
    )
    resultado: dict[int, int] = {}
    for item in resumir(sessao, faturas, hoje):
        if item.restante:
            conta_id = item.fatura.cartao.conta_pagamento_id
            resultado[conta_id] = resultado.get(conta_id, 0) + item.restante
    return resultado


def lancamentos_da_fatura(sessao: Session, fatura: Fatura) -> list[Lancamento]:
    return list(
        sessao.scalars(
            select(Lancamento)
            .options(
                joinedload(Lancamento.categoria).joinedload(Categoria.pai),
                joinedload(Lancamento.conta),
            )
            .where(Lancamento.fatura_id == fatura.id)
            .order_by(Lancamento.data.desc(), Lancamento.id.desc())
        )
    )


# ───────── Cadastro ─────────


def listar(sessao: Session, incluir_arquivados: bool = False) -> list[Cartao]:
    consulta = select(Cartao).order_by(Cartao.ativo.desc(), Cartao.nome)
    if not incluir_arquivados:
        consulta = consulta.where(Cartao.ativo)
    return list(sessao.scalars(consulta))


def salvar(sessao: Session, dados: Mapping[str, str], cartao: Cartao | None = None) -> Cartao:
    leitor = Leitor(dados)
    nome = leitor.texto("nome", maximo=60)
    bandeira = leitor.texto("bandeira", maximo=30, obrigatorio=False)
    limite_centavos = leitor.centavos("limite")
    dia_fechamento = leitor.inteiro("dia_fechamento", 1, 31)
    dia_vencimento = leitor.inteiro("dia_vencimento", 1, 31)
    regra = leitor.escolha("compra_no_fechamento", CompraNoFechamento)
    conta_id = leitor.id("conta_pagamento_id")
    conta = sessao.get(Conta, conta_id) if conta_id else None
    if conta_id and (
        conta is None or not (conta.ativa or (cartao and cartao.conta_pagamento_id == conta_id))
    ):
        leitor.erros["conta_pagamento_id"] = "Conta inválida ou arquivada."
    if nome:
        repetido = sessao.scalar(
            select(Cartao.id).where(
                func.lower(Cartao.nome) == nome.lower(),
                Cartao.id != (cartao.id if cartao else -1),
            )
        )
        if repetido:
            leitor.erros["nome"] = "Já existe um cartão com esse nome."
    leitor.verificar()

    novo = cartao is None
    if novo:
        cartao = Cartao()
        sessao.add(cartao)
    mudou_ciclo = not novo and (
        cartao.dia_fechamento != dia_fechamento
        or cartao.dia_vencimento != dia_vencimento
        or cartao.compra_no_fechamento != regra
    )
    cartao.nome = nome
    cartao.bandeira = bandeira
    cartao.limite_centavos = limite_centavos
    cartao.dia_fechamento = dia_fechamento
    cartao.dia_vencimento = dia_vencimento
    cartao.compra_no_fechamento = regra
    cartao.conta_pagamento = conta
    sessao.flush()
    if mudou_ciclo:
        _redistribuir(sessao, cartao)
    return cartao


def _redistribuir(sessao: Session, cartao: Cartao) -> None:
    """Depois de mudar o ciclo, recoloca cada compra na fatura certa e ajusta as datas."""
    for fatura in faturas_do_cartao(sessao, cartao):
        fatura.data_fechamento, fatura.data_vencimento = datas_da_fatura(
            cartao, Competencia.de_texto(fatura.referencia)
        )
    compras = sessao.scalars(
        select(Lancamento).where(
            Lancamento.cartao_id == cartao.id, Lancamento.tipo != TipoLancamento.TRANSFERENCIA
        )
    )
    for compra in compras:
        compra.fatura = fatura_da_compra(sessao, cartao, compra.data)
    sessao.flush()


def definir_ativo(sessao: Session, cartao: Cartao, ativo: bool) -> None:
    cartao.ativo = ativo
    sessao.flush()


# ───────── Pagamento da fatura (E2-19) ─────────


def pagar(
    sessao: Session,
    fatura: Fatura,
    dados: Mapping[str, str],
    pagamento: Lancamento | None = None,
) -> Lancamento:
    """Registra (ou edita) um pagamento: transferência da conta para o cartão.

    Pode ser parcial; estornar o pagamento = excluí-lo (com Desfazer).
    """
    leitor = Leitor(dados)
    valor = leitor.centavos("valor")
    dia = leitor.data("data")
    conta_id = leitor.id("conta_id")
    conta = sessao.get(Conta, conta_id) if conta_id else None
    if conta_id and (
        conta is None or not (conta.ativa or (pagamento and pagamento.conta_id == conta_id))
    ):
        leitor.erros["conta_id"] = "Conta inválida ou arquivada."
    leitor.verificar()

    referencia = Competencia.de_texto(fatura.referencia)
    if pagamento is None:
        pagamento = Lancamento(
            tipo=TipoLancamento.TRANSFERENCIA,
            cartao_id=fatura.cartao_id,
            fatura=fatura,
            descricao=f"Fatura {fatura.cartao.nome} · {referencia.rotulo_curto}",
            pago=True,
        )
        sessao.add(pagamento)
    pagamento.conta = conta
    pagamento.valor_centavos = valor
    pagamento.data = dia
    pagamento.competencia = str(Competencia.de_data(dia))
    sessao.flush()
    return pagamento

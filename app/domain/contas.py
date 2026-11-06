"""Contas e saldos.

Saldo de uma conta = saldo inicial + efeito dos lançamentos com data ≥ `data_saldo_inicial`:
entrada soma, saída subtrai, transferência subtrai da origem e soma no destino.
Lançamentos anteriores à data do saldo inicial ficam só como histórico, e os retroativos
(parcelas já pagas antes de serem lançadas, E2-23) também: o saldo informado já os considera.
Operações da carteira com conta (E4-03) também movem o saldo: compra tira, venda e
proventos põem.
"""

from collections.abc import Mapping
from datetime import date

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Conta, Lancamento, Operacao, TipoConta, TipoLancamento, TipoOperacao
from app.domain.formularios import Leitor

MOVIMENTOS_DE_ENTRADA = [
    TipoOperacao.VENDA,
    TipoOperacao.DIVIDENDO,
    TipoOperacao.JCP,
    TipoOperacao.RENDIMENTO,
]


def listar(sessao: Session, incluir_arquivadas: bool = False) -> list[Conta]:
    consulta = select(Conta).order_by(Conta.ativa.desc(), Conta.nome)
    if not incluir_arquivadas:
        consulta = consulta.where(Conta.ativa)
    return list(sessao.scalars(consulta))


def salvar(sessao: Session, dados: Mapping[str, str], conta: Conta | None = None) -> Conta:
    leitor = Leitor(dados)
    nome = leitor.texto("nome", maximo=60)
    tipo = leitor.escolha("tipo", TipoConta)
    saldo = leitor.centavos("saldo_inicial", positivo=False, obrigatorio=False)
    data_saldo = leitor.data("data_saldo_inicial")
    if nome:
        em_uso = sessao.scalar(
            select(Conta.id).where(func.lower(Conta.nome) == nome.lower(), Conta.id != _id(conta))
        )
        if em_uso:
            leitor.erros["nome"] = "Já existe uma conta com esse nome."
    leitor.verificar()

    if conta is None:
        conta = Conta()
        sessao.add(conta)
    conta.nome = nome
    conta.tipo = tipo
    conta.saldo_inicial_centavos = saldo or 0
    conta.data_saldo_inicial = data_saldo
    conta.reserva_emergencia = leitor.marcado("reserva_emergencia")
    sessao.flush()
    return conta


def definir_ativa(sessao: Session, conta: Conta, ativa: bool) -> None:
    """Arquivar em vez de apagar: o histórico de lançamentos continua íntegro."""
    conta.ativa = ativa
    sessao.flush()


def saldos(sessao: Session, ate: date, incluir_pendentes: bool = False) -> dict[int, int]:
    """Saldo em centavos de cada conta ao fim do dia `ate`."""
    valor = Lancamento.valor_centavos
    filtro_base = [Lancamento.data <= ate, ~Lancamento.retroativo]
    if not incluir_pendentes:
        filtro_base.append(Lancamento.pago)

    efeito_origem = case((Lancamento.tipo == TipoLancamento.ENTRADA, valor), else_=-valor)
    origem = (
        select(Lancamento.conta_id, func.sum(efeito_origem))
        .join(Conta, Conta.id == Lancamento.conta_id)
        .where(*filtro_base, Lancamento.data >= Conta.data_saldo_inicial)
        .group_by(Lancamento.conta_id)
    )
    destino = (
        select(Lancamento.conta_destino_id, func.sum(valor))
        .join(Conta, Conta.id == Lancamento.conta_destino_id)
        .where(
            *filtro_base,
            Lancamento.tipo == TipoLancamento.TRANSFERENCIA,
            Lancamento.data >= Conta.data_saldo_inicial,
        )
        .group_by(Lancamento.conta_destino_id)
    )

    resultado = {
        conta.id: conta.saldo_inicial_centavos
        for conta in sessao.scalars(select(Conta).where(Conta.data_saldo_inicial <= ate))
    }
    efeito_operacao = case(
        (Operacao.tipo == TipoOperacao.COMPRA, -Operacao.valor_total_centavos),
        (Operacao.tipo.in_(MOVIMENTOS_DE_ENTRADA), Operacao.valor_total_centavos),
        else_=0,
    )
    investimentos = (
        select(Operacao.conta_id, func.sum(efeito_operacao))
        .join(Conta, Conta.id == Operacao.conta_id)
        .where(Operacao.data <= ate, Operacao.data >= Conta.data_saldo_inicial)
        .group_by(Operacao.conta_id)
    )
    linhas = [*sessao.execute(origem), *sessao.execute(destino), *sessao.execute(investimentos)]
    for conta_id, soma in linhas:
        if conta_id in resultado:
            resultado[conta_id] += soma or 0
    return resultado


def em_uso(sessao: Session, conta: Conta) -> bool:
    return bool(
        sessao.scalar(
            select(Lancamento.id)
            .where(or_(Lancamento.conta_id == conta.id, Lancamento.conta_destino_id == conta.id))
            .limit(1)
        )
    )


def _id(objeto) -> int:
    return objeto.id if objeto is not None and objeto.id is not None else -1

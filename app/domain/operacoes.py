"""Registro de operações da carteira (E4-03)."""

from collections.abc import Mapping
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.models import Ativo, Conta, Desfazer, Operacao, TipoOperacao
from app.domain import carteira
from app.domain.ativos import centavos, ler_decimal
from app.domain.desfazer import excluir_com_desfazer
from app.domain.formularios import Leitor

# Tipos que podem mexer no saldo de uma conta (posição inicial e eventos, não).
MOVEM_CONTA = {
    TipoOperacao.COMPRA,
    TipoOperacao.VENDA,
    TipoOperacao.DIVIDENDO,
    TipoOperacao.JCP,
    TipoOperacao.RENDIMENTO,
}


def salvar(sessao: Session, dados: Mapping[str, str], operacao: Operacao | None = None) -> Operacao:
    leitor = Leitor(dados)
    ativo_id = leitor.id("ativo_id")
    ativo = sessao.get(Ativo, ativo_id) if ativo_id else None
    if ativo_id and ativo is None:
        leitor.erros["ativo_id"] = "Ativo inválido."
    tipo = leitor.escolha("tipo", TipoOperacao)
    data = leitor.data("data")
    observacao = leitor.texto("observacao", maximo=500, obrigatorio=False)
    quantidade = preco = None
    taxas = 0
    valor = 0

    if tipo in (TipoOperacao.POSICAO_INICIAL, TipoOperacao.COMPRA, TipoOperacao.VENDA):
        quantidade = ler_decimal(leitor, "quantidade")
        preco = ler_decimal(leitor, "preco_unitario", positivo=False)
        if preco is not None and preco < 0:
            leitor.erros["preco_unitario"] = "O preço não pode ser negativo."
        if tipo != TipoOperacao.POSICAO_INICIAL:
            taxas = leitor.centavos("taxas", positivo=False, obrigatorio=False) or 0
            if taxas < 0:
                leitor.erros["taxas"] = "As taxas não podem ser negativas."
        if quantidade and preco is not None:
            bruto = centavos(quantidade * preco)
            valor = bruto - taxas if tipo == TipoOperacao.VENDA else bruto + taxas
            if valor < 0:
                leitor.erros["taxas"] = "As taxas passam do valor da venda."
    elif tipo and tipo.provento:
        valor = leitor.centavos("valor") or 0
        quantidade = ler_decimal(leitor, "quantidade", obrigatorio=False)
    elif tipo == TipoOperacao.DESDOBRAMENTO:
        quantidade = ler_decimal(leitor, "quantidade")
        if quantidade == 1:
            leitor.erros["quantidade"] = (
                "Fator 1 não muda nada. Ex.: 2 (desdobramento 1:2), 0,1 (grupamento 10:1)."
            )
    elif tipo == TipoOperacao.BONIFICACAO:
        quantidade = ler_decimal(leitor, "quantidade")
        preco = ler_decimal(leitor, "preco_unitario", obrigatorio=False, positivo=False) or Decimal(
            0
        )
        if quantidade:
            valor = centavos(quantidade * preco)

    conta = None
    if tipo in MOVEM_CONTA and leitor.bruto("conta_id"):
        conta_id = leitor.id("conta_id")
        conta = sessao.get(Conta, conta_id) if conta_id else None
        if conta_id and (
            conta is None or not (conta.ativa or (operacao and operacao.conta_id == conta_id))
        ):
            leitor.erros["conta_id"] = "Conta inválida ou arquivada."

    # Venda e eventos precisam de posição na data (sem contar a própria operação, ao editar).
    if (
        ativo
        and data
        and tipo in (TipoOperacao.VENDA, TipoOperacao.DESDOBRAMENTO, TipoOperacao.BONIFICACAO)
    ):
        anteriores = [o for o in carteira.operacoes(sessao, data, ativo.id)]
        posicao = carteira.calcular(anteriores, ignorar_id=operacao.id if operacao else None).get(
            ativo.id
        )
        disponivel = posicao.quantidade if posicao else Decimal(0)
        if disponivel <= 0:
            leitor.erros["data"] = f"Não há {ativo.ticker} na carteira nessa data."
        elif tipo == TipoOperacao.VENDA and quantidade and quantidade > disponivel:
            leitor.erros["quantidade"] = f"Há só {formatar_quantidade(disponivel)} nessa data."
    leitor.verificar()

    if operacao is None:
        operacao = Operacao()
        sessao.add(operacao)
    operacao.ativo = ativo
    operacao.tipo = tipo
    operacao.data = data
    operacao.quantidade = quantidade
    operacao.preco_unitario = preco
    operacao.taxas_centavos = taxas
    operacao.valor_total_centavos = valor
    operacao.conta = conta
    operacao.observacao = observacao
    sessao.flush()
    return operacao


def excluir(sessao: Session, operacao: Operacao) -> Desfazer:
    descricao = f"{operacao.tipo.rotulo} de {operacao.ativo.ticker} em {operacao.data:%d/%m/%Y}"
    return excluir_com_desfazer(sessao, [operacao], descricao)


def formatar_quantidade(valor: Decimal | int | None) -> str:
    """1234.5 → '1.234,5'; inteiros sem casas."""
    if valor is None:
        return ""
    normalizado = Decimal(valor).normalize()
    if normalizado == normalizado.to_integral():
        normalizado = normalizado.quantize(Decimal(1))
    inteiro, _, fracao = format(normalizado, "f").partition(".")
    sinal = "−" if inteiro.startswith("-") else ""
    inteiro = f"{int(inteiro.lstrip('-')):,}".replace(",", ".")
    return sinal + inteiro + ("," + fracao if fracao else "")

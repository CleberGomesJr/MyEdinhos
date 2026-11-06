"""Assistente de cadastro inicial (E2-22): começar sem lançar o histórico.

Passo 1 — saldo de hoje de cada conta: ajusta o saldo inicial para o saldo de hoje bater.
Passo 2 — cartões: o total das faturas em aberto entra como um único lançamento de ajuste
("Compras anteriores ao MyEdinhos"), e os parcelamentos em andamento geram só as parcelas
que ainda vão cair nas próximas faturas.
Passo 3 — carteira: chega com o E4 (E4-15).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db.models import (
    Cartao,
    Categoria,
    Conta,
    Fatura,
    Lancamento,
    TipoCategoria,
    TipoLancamento,
)
from app.domain import cartoes, contas, preferencias
from app.domain.competencia import Competencia
from app.domain.datas import somar_meses
from app.domain.dinheiro import formatar_brl
from app.domain.formularios import ErroValidacao, Leitor

PREFERENCIA = "assistente_inicial"  # "concluido" ou "dispensado"
DESCRICAO_AJUSTE = "Compras anteriores ao MyEdinhos"
OBSERVACAO_AJUSTE = "Criado pelo assistente de cadastro inicial: total da fatura informado."
CATEGORIA_AJUSTE = "Outros gastos"


def pendente(sessao: Session) -> bool:
    return preferencias.obter(sessao, PREFERENCIA) is None


def encerrar(sessao: Session, como: str = "concluido") -> None:
    preferencias.definir(sessao, PREFERENCIA, como)


# ───────── Passo 1: saldos ─────────


def definir_saldo_hoje(sessao: Session, conta: Conta, saldo: int, hoje: date) -> None:
    """Ajusta o saldo inicial para que o saldo de hoje seja `saldo`.

    Conta sem lançamentos: o saldo passa a valer no início de hoje. Com lançamentos, mantém a
    data e corrige o valor inicial pela diferença (os lançamentos continuam movendo o saldo).
    """
    atual = contas.saldos(sessao, hoje).get(conta.id)
    if atual is None or not contas.em_uso(sessao, conta):
        conta.saldo_inicial_centavos = saldo
        conta.data_saldo_inicial = hoje
    else:
        conta.saldo_inicial_centavos += saldo - atual
    sessao.flush()


def salvar_saldos(sessao: Session, dados: Mapping[str, str], hoje: date) -> list[Conta]:
    """Campos `saldo-<id>` de cada conta ativa; em branco = não mexer."""
    leitor = Leitor(dados)
    novos = {
        conta: leitor.centavos(f"saldo-{conta.id}", positivo=False, obrigatorio=False)
        for conta in contas.listar(sessao)
    }
    leitor.verificar()
    atuais = contas.saldos(sessao, hoje)
    alteradas = []
    for conta, saldo in novos.items():
        if saldo is not None and saldo != atuais.get(conta.id):
            definir_saldo_hoje(sessao, conta, saldo, hoje)
            alteradas.append(conta)
    return alteradas


# ───────── Passo 2: faturas em aberto ─────────


def faturas_em_aberto(sessao: Session, cartao: Cartao, hoje: date) -> list[Fatura]:
    """A fatura fechada que ainda não venceu (se houver) e a fatura aberta hoje."""
    atual = cartoes.fatura_atual(sessao, cartao, hoje)
    anterior = Competencia.de_texto(atual.referencia).anterior()
    _, vencimento = cartoes.datas_da_fatura(cartao, anterior)
    if hoje <= vencimento:
        return [cartoes.obter_fatura(sessao, cartao, anterior), atual]
    return [atual]


def data_na_fatura(cartao: Cartao, referencia: Competencia, sugestao: date) -> date:
    """`sugestao` se uma compra nesse dia cair na fatura `referencia`; senão o último dia
    do ciclo dela (o fechamento, ou a véspera quando a compra do fechamento vai para a próxima)."""
    if cartoes.referencia_da_compra(cartao, sugestao) == referencia:
        return sugestao
    fechamento, _ = cartoes.datas_da_fatura(cartao, referencia)
    if cartoes.referencia_da_compra(cartao, fechamento) == referencia:
        return fechamento
    return fechamento - timedelta(days=1)


def ajuste_da_fatura(sessao: Session, fatura: Fatura) -> Lancamento | None:
    return sessao.scalar(
        select(Lancamento).where(
            Lancamento.fatura_id == fatura.id,
            Lancamento.tipo == TipoLancamento.SAIDA,
            Lancamento.observacao == OBSERVACAO_AJUSTE,
        )
    )


def _categoria_ajuste(sessao: Session) -> Categoria | None:
    saidas = list(
        sessao.scalars(
            select(Categoria)
            .where(Categoria.tipo == TipoCategoria.SAIDA, Categoria.ativa)
            .order_by(Categoria.nome != CATEGORIA_AJUSTE, Categoria.cor_slot != 0, Categoria.id)
        )
    )
    return saidas[0] if saidas else None


def definir_total_fatura(sessao: Session, fatura: Fatura, total: int, hoje: date) -> str | None:
    """Faz a fatura somar `total`, criando/ajustando o lançamento de ajuste.

    Devolve uma mensagem de erro se `total` for menor que o já lançado nela.
    """
    ajuste = ajuste_da_fatura(sessao, fatura)
    ja_lancado = cartoes.resumo(sessao, fatura, hoje).total - (
        ajuste.valor_centavos if ajuste else 0
    )
    diferenca = total - ja_lancado
    if diferenca < 0:
        return (
            f"Já há {formatar_brl(ja_lancado)} lançados nesta fatura. "
            "Informe pelo menos isso ou edite as compras."
        )
    if diferenca == 0:
        if ajuste:
            sessao.delete(ajuste)
            sessao.flush()
        return None
    if ajuste is None:
        categoria = _categoria_ajuste(sessao)
        if categoria is None:
            return "Cadastre uma categoria de saída antes."
        referencia = Competencia.de_texto(fatura.referencia)
        dia = data_na_fatura(fatura.cartao, referencia, min(hoje, fatura.data_fechamento))
        ajuste = Lancamento(
            tipo=TipoLancamento.SAIDA,
            descricao=DESCRICAO_AJUSTE,
            data=dia,
            competencia=str(Competencia.de_data(dia)),
            cartao=fatura.cartao,
            fatura=fatura,
            categoria=categoria,
            pago=True,
            observacao=OBSERVACAO_AJUSTE,
        )
        sessao.add(ajuste)
    ajuste.valor_centavos = diferenca
    sessao.flush()
    return None


def salvar_faturas(sessao: Session, dados: Mapping[str, str], hoje: date) -> None:
    """Campos `fatura-<id>` com o total de cada fatura em aberto; em branco = não mexer."""
    leitor = Leitor(dados)
    totais = {}
    for cartao in cartoes.listar(sessao):
        for fatura in faturas_em_aberto(sessao, cartao, hoje):
            total = leitor.centavos(f"fatura-{fatura.id}", positivo=False, obrigatorio=False)
            if total is not None and total < 0:
                leitor.erros[f"fatura-{fatura.id}"] = "O total não pode ser negativo."
            elif total is not None:
                totais[fatura] = total
    leitor.verificar()
    erros = {}
    for fatura, total in totais.items():
        if erro := definir_total_fatura(sessao, fatura, total, hoje):
            erros[f"fatura-{fatura.id}"] = erro
    if erros:
        raise ErroValidacao(erros)


# ───────── Passo 2: parcelamentos em andamento ─────────


@dataclass
class Parcelamento:
    descricao: str
    valor: int  # de cada parcela
    proxima: Lancamento  # a próxima parcela (excluí-la com "e as próximas" encerra o resto)
    restantes: int

    @property
    def total(self) -> int:
        return self.proxima.parcela_total


def parcelamentos_em_andamento(sessao: Session, cartao: Cartao, hoje: date) -> list[Parcelamento]:
    """Parcelas que ainda vão cair em faturas depois da atual, agrupadas por compra."""
    atual = cartoes.fatura_atual(sessao, cartao, hoje)
    parcelas = sessao.scalars(
        select(Lancamento)
        .join(Fatura, Fatura.id == Lancamento.fatura_id)
        .options(joinedload(Lancamento.fatura))
        .where(
            Lancamento.cartao_id == cartao.id,
            Lancamento.parcela_grupo_id.is_not(None),
            Fatura.referencia > atual.referencia,
        )
        .order_by(Lancamento.parcela_n)
    )
    grupos: dict[str, Parcelamento] = {}
    for parcela in parcelas:
        grupo = grupos.get(parcela.parcela_grupo_id)
        if grupo is None:
            grupos[parcela.parcela_grupo_id] = Parcelamento(
                parcela.descricao, parcela.valor_centavos, parcela, 1
            )
        else:
            grupo.restantes += 1
    return sorted(grupos.values(), key=lambda item: item.proxima.data)


def adicionar_parcelamento(
    sessao: Session, cartao: Cartao, dados: Mapping[str, str], hoje: date
) -> list[Lancamento]:
    """Compra parcelada já em andamento: "parcela 3 de 10" na fatura atual gera as parcelas
    4 a 10, uma em cada fatura seguinte. A da fatura atual já está no total dela."""
    leitor = Leitor(dados)
    descricao = leitor.texto("descricao", maximo=120)
    valor = leitor.centavos("valor")
    atual_n = leitor.inteiro("parcela_atual", 1, 119)
    total_n = leitor.inteiro("parcelas", 2, 120)
    categoria_id = leitor.id("categoria_id")
    categoria = sessao.get(Categoria, categoria_id) if categoria_id else None
    if categoria_id and (
        categoria is None or not categoria.ativa or categoria.tipo != TipoCategoria.SAIDA
    ):
        leitor.erros["categoria_id"] = "Escolha uma categoria de saída."
    if atual_n and total_n and atual_n >= total_n:
        leitor.erros["parcela_atual"] = "Não há parcelas depois desta."
    leitor.verificar()

    referencia = Competencia.de_texto(cartoes.fatura_atual(sessao, cartao, hoje).referencia)
    grupo = uuid4().hex
    criadas = []
    for indice in range(1, total_n - atual_n + 1):
        referencia = referencia.proxima()
        dia = data_na_fatura(cartao, referencia, somar_meses(hoje, indice))
        parcela = Lancamento(
            tipo=TipoLancamento.SAIDA,
            descricao=descricao,
            valor_centavos=valor,
            data=dia,
            competencia=str(Competencia.de_data(dia)),
            cartao=cartao,
            fatura=cartoes.obter_fatura(sessao, cartao, referencia),
            categoria=categoria,
            pago=True,
            parcela_grupo_id=grupo,
            parcela_n=atual_n + indice,
            parcela_total=total_n,
        )
        sessao.add(parcela)
        criadas.append(parcela)
    sessao.flush()
    return criadas

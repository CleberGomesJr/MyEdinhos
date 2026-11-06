"""Cartão de crédito e faturas (E2-21): virada de fechamento, parcelas, pagamento e limite."""

from datetime import date

import pytest
from sqlalchemy import select

from app.db.models import Cartao, Categoria, CompraNoFechamento, Conta, Lancamento, Recorrencia
from app.domain import cartoes, contas, lancamentos, recorrencias
from app.domain.cartoes import Situacao
from app.domain.competencia import Competencia
from app.domain.formularios import ErroValidacao

OUTUBRO = Competencia(2026, 10)


@pytest.fixture
def conta(sessao) -> Conta:
    conta = sessao.scalar(select(Conta))
    contas.salvar(
        sessao,
        {"nome": conta.nome, "tipo": "corrente", "saldo_inicial": "5.000,00",
         "data_saldo_inicial": "2026-09-01"},
        conta,
    )  # fmt: skip
    return conta


@pytest.fixture
def cartao(sessao, conta) -> Cartao:
    """Cartão de exemplo: fecha dia 19, vence dia 26, limite R$ 10.000."""
    return cartoes.salvar(
        sessao,
        {"nome": "Cartão Teste", "limite": "10.000,00", "dia_fechamento": "19",
         "dia_vencimento": "26", "compra_no_fechamento": "fatura_atual",
         "conta_pagamento_id": str(conta.id)},
    )  # fmt: skip


def _mercado(sessao) -> int:
    return sessao.scalar(select(Categoria.id).where(Categoria.nome == "Mercado"))


def _comprar(sessao, cartao, dia: str, valor: str = "100,00", **extra) -> list[Lancamento]:
    dados = {
        "tipo": "saida",
        "meio": "cartao",
        "cartao_id": str(cartao.id),
        "valor": valor,
        "descricao": "Compra",
        "data": dia,
        "categoria_id": str(_mercado(sessao)),
        **{chave: str(valor_extra) for chave, valor_extra in extra.items()},
    }
    return lancamentos.salvar(sessao, dados)


# ───────── Ciclo da fatura (E2-15 / E2-16) ─────────


@pytest.mark.parametrize(
    ("compra", "referencia"),
    [
        (date(2026, 9, 20), "2026-10"),  # dia seguinte ao fechamento → próxima fatura
        (date(2026, 10, 18), "2026-10"),
        (date(2026, 10, 19), "2026-10"),  # no dia do fechamento (regra "fatura atual")
        (date(2026, 10, 20), "2026-11"),
        (date(2026, 12, 25), "2027-01"),  # virada de ano
    ],
)
def test_compra_cai_na_fatura_certa(cartao, compra, referencia):
    assert str(cartoes.referencia_da_compra(cartao, compra)) == referencia


def test_regra_proxima_fatura_no_dia_do_fechamento(cartao):
    cartao.compra_no_fechamento = CompraNoFechamento.PROXIMA_FATURA
    assert str(cartoes.referencia_da_compra(cartao, date(2026, 10, 19))) == "2026-11"


def test_datas_da_fatura(cartao):
    assert cartoes.datas_da_fatura(cartao, OUTUBRO) == (date(2026, 10, 19), date(2026, 10, 26))


def test_vencimento_antes_do_fechamento_vence_no_mes_seguinte(cartao):
    cartao.dia_fechamento, cartao.dia_vencimento = 28, 5
    assert str(cartoes.referencia_da_compra(cartao, date(2026, 10, 10))) == "2026-11"
    assert cartoes.datas_da_fatura(cartao, Competencia(2026, 11)) == (
        date(2026, 10, 28), date(2026, 11, 5),
    )  # fmt: skip


def test_fechamento_dia_31_em_mes_curto(cartao):
    cartao.dia_fechamento, cartao.dia_vencimento = 31, 10
    fechamento, vencimento = cartoes.datas_da_fatura(cartao, Competencia(2026, 3))
    assert fechamento == date(2026, 2, 28) and vencimento == date(2026, 3, 10)


# ───────── Compras e parcelas (E2-16 / E2-17) ─────────


def test_compra_no_cartao_e_despesa_do_mes_mas_nao_mexe_no_saldo(sessao, conta, cartao):
    (compra,) = _comprar(sessao, cartao, "2026-10-21", "250,00")
    assert compra.conta_id is None and compra.pago
    assert compra.fatura.referencia == "2026-11"
    assert contas.saldos(sessao, date(2026, 10, 31))[conta.id] == 500000
    resumo_mes = lancamentos.totais(
        lancamentos.listar(sessao, OUTUBRO, lancamentos.Filtros(), date(2026, 10, 29))
    )
    assert resumo_mes.saidas == 25000


def test_parcelas_em_faturas_sucessivas(sessao, cartao):
    parcelas = _comprar(sessao, cartao, "2026-10-25", "900,00", repeticao="parcelado", parcelas="3")
    assert [p.fatura.referencia for p in parcelas] == ["2026-11", "2026-12", "2027-01"]
    assert [p.competencia for p in parcelas] == ["2026-10", "2026-11", "2026-12"]
    assert all(p.pago for p in parcelas)


def test_editar_data_muda_a_fatura(sessao, cartao):
    (compra,) = _comprar(sessao, cartao, "2026-10-10")
    assert compra.fatura.referencia == "2026-10"
    lancamentos.salvar(
        sessao,
        {"tipo": "saida", "meio": "cartao", "cartao_id": str(cartao.id), "valor": "100,00",
         "descricao": "Compra", "data": "2026-10-21", "categoria_id": str(_mercado(sessao))},
        compra,
    )  # fmt: skip
    assert compra.fatura.referencia == "2026-11"


def test_cartao_obrigatorio_quando_meio_e_cartao(sessao, cartao):
    with pytest.raises(ErroValidacao) as erro:
        _comprar(sessao, cartao, "2026-10-10", cartao_id="")
    assert "cartao_id" in erro.value.erros


def test_assinatura_recorrente_no_cartao(sessao, cartao):
    _comprar(sessao, cartao, "2026-10-05", "39,90", repeticao="mensal", descricao="Streaming")
    regra = sessao.scalar(select(Recorrencia))
    assert regra.cartao_id == cartao.id and regra.conta_id is None
    assert recorrencias.gerar_pendentes(sessao, date(2026, 12, 31)) == 2
    geradas = sessao.scalars(
        select(Lancamento).where(Lancamento.recorrencia_id == regra.id).order_by(Lancamento.data)
    ).all()
    assert [g.fatura.referencia for g in geradas] == ["2026-10", "2026-11", "2026-12"]
    assert all(g.pago for g in geradas)
    assert recorrencias.encerrar(sessao, regra, date(2026, 10, 29)) == 2


# ───────── Pagamento sem dupla contagem (E2-19) ─────────


def test_pagamento_total_tira_da_conta_e_nao_e_despesa(sessao, conta, cartao):
    _comprar(sessao, cartao, "2026-09-25", "300,00")
    _comprar(sessao, cartao, "2026-10-02", "200,00")
    fatura = cartoes.obter_fatura(sessao, cartao, OUTUBRO)

    antes = cartoes.resumo(sessao, fatura, date(2026, 10, 20))
    assert (antes.total, antes.restante, antes.situacao) == (50000, 50000, Situacao.FECHADA)

    pagamento = cartoes.pagar(
        sessao, fatura, {"valor": "500,00", "data": "2026-10-26", "conta_id": str(conta.id)}
    )
    assert pagamento.pagamento_de_fatura
    depois = cartoes.resumo(sessao, fatura, date(2026, 10, 27))
    assert (depois.pago, depois.restante, depois.situacao) == (50000, 0, Situacao.PAGA)

    # Saldo cai uma vez (no pagamento); o mês de outubro só conta a compra de outubro.
    assert contas.saldos(sessao, date(2026, 10, 31))[conta.id] == 500000 - 50000
    itens = lancamentos.listar(sessao, OUTUBRO, lancamentos.Filtros(), date(2026, 10, 29))
    assert lancamentos.totais(itens).saidas == 20000


def test_pagamento_parcial_e_fatura_vencida(sessao, conta, cartao):
    _comprar(sessao, cartao, "2026-10-01", "1.000,00")
    fatura = cartoes.obter_fatura(sessao, cartao, OUTUBRO)
    cartoes.pagar(
        sessao, fatura, {"valor": "400,00", "data": "2026-10-26", "conta_id": str(conta.id)}
    )
    resumo = cartoes.resumo(sessao, fatura, date(2026, 10, 27))
    assert (resumo.restante, resumo.situacao) == (60000, Situacao.VENCIDA)


def test_estorno_do_pagamento_reabre_a_fatura(sessao, conta, cartao):
    _comprar(sessao, cartao, "2026-10-01", "100,00")
    fatura = cartoes.obter_fatura(sessao, cartao, OUTUBRO)
    pagamento = cartoes.pagar(
        sessao, fatura, {"valor": "100,00", "data": "2026-10-26", "conta_id": str(conta.id)}
    )
    lancamentos.excluir(sessao, pagamento)
    assert cartoes.resumo(sessao, fatura, date(2026, 10, 25)).restante == 10000
    assert contas.saldos(sessao, date(2026, 10, 31))[conta.id] == 500000


def test_credito_no_cartao_reduz_a_fatura(sessao, cartao):
    _comprar(sessao, cartao, "2026-10-01", "100,00")
    salario = sessao.scalar(select(Categoria.id).where(Categoria.nome == "Outras entradas"))
    _comprar(sessao, cartao, "2026-10-03", "30,00", tipo="entrada", categoria_id=salario)
    fatura = cartoes.obter_fatura(sessao, cartao, OUTUBRO)
    assert cartoes.resumo(sessao, fatura, date(2026, 10, 5)).total == 7000


# ───────── Limite e previsão (E2-21) ─────────


def test_limite_disponivel_separa_atual_e_parcelas_futuras(sessao, conta, cartao):
    hoje = date(2026, 10, 29)
    _comprar(sessao, cartao, "2026-10-10", "500,00")  # fatura de outubro (fechada, não paga)
    _comprar(sessao, cartao, "2026-10-25", "1.200,00", repeticao="parcelado", parcelas="3")
    limite = cartoes.limite(sessao, cartao, hoje)
    # Aberta/fechadas: outubro (500) + novembro (1ª parcela, 400). Futuras: dez + jan (800).
    assert (limite.faturas_abertas, limite.parcelas_futuras) == (90000, 80000)
    assert limite.disponivel == 1000000 - 170000

    fatura_outubro = cartoes.obter_fatura(sessao, cartao, OUTUBRO)
    cartoes.pagar(
        sessao, fatura_outubro, {"valor": "500,00", "data": "2026-10-26", "conta_id": str(conta.id)}
    )
    assert cartoes.limite(sessao, cartao, hoje).disponivel == 1000000 - 120000


def test_previsto_da_conta_considera_faturas_a_vencer(sessao, conta, cartao):
    _comprar(sessao, cartao, "2026-10-25", "300,00")  # vence 26/11
    assert cartoes.a_pagar_por_conta(sessao, date(2026, 10, 31), date(2026, 10, 29)) == {}
    assert cartoes.a_pagar_por_conta(sessao, date(2026, 11, 30), date(2026, 10, 29)) == {
        conta.id: 30000
    }


def test_mudar_o_fechamento_redistribui_as_compras(sessao, conta, cartao):
    (compra,) = _comprar(sessao, cartao, "2026-10-15")
    assert compra.fatura.referencia == "2026-10"
    cartoes.salvar(
        sessao,
        {"nome": cartao.nome, "limite": "10.000,00", "dia_fechamento": "10", "dia_vencimento": "17",
         "compra_no_fechamento": "fatura_atual", "conta_pagamento_id": str(conta.id)},
        cartao,
    )  # fmt: skip
    assert compra.fatura.referencia == "2026-11"
    assert compra.fatura.data_vencimento == date(2026, 11, 17)


def test_cartao_com_nome_repetido(sessao, conta, cartao):
    with pytest.raises(ErroValidacao) as erro:
        cartoes.salvar(
            sessao,
            {"nome": "cartão teste", "limite": "1,00", "dia_fechamento": "1", "dia_vencimento": "8",
             "compra_no_fechamento": "fatura_atual", "conta_pagamento_id": str(conta.id)},
        )  # fmt: skip
    assert "nome" in erro.value.erros


# ───────── Compra retroativa (E2-23) ─────────

HOJE = date(2026, 11, 1)


def _retroativa(sessao, dia="2026-11-01", pagas="7", parcelas="10", valor="1.000,00", **extra):
    """Compra em `parcelas` vezes em que `pagas` já foram quitadas; `dia` é a parcela atual."""
    dados = {
        "tipo": "saida",
        "descricao": "Teclado",
        "valor": valor,
        "data": dia,
        "categoria_id": str(_mercado(sessao)),
        "repeticao": "parcelado",
        "parcelas": parcelas,
        "retroativo": "1",
        "parcelas_pagas": pagas,
        **{chave: str(valor_extra) for chave, valor_extra in extra.items()},
    }
    return lancamentos.salvar(sessao, dados, hoje=HOJE)


def test_retroativa_no_cartao_espalha_as_parcelas_pelas_faturas(sessao, conta, cartao):
    parcelas = _retroativa(sessao, meio="cartao", cartao_id=cartao.id)

    assert len(parcelas) == 10
    assert [p.parcela_n for p in parcelas] == list(range(1, 11))
    assert [p.fatura.referencia for p in parcelas] == [
        "2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10",
        "2026-11", "2026-12", "2027-01",
    ]  # fmt: skip
    assert [p.data for p in parcelas][:2] == [date(2026, 4, 1), date(2026, 5, 1)]
    assert [p.retroativo for p in parcelas] == [True] * 7 + [False] * 3
    assert all(p.pago and p.conta_id is None for p in parcelas)
    assert sum(p.valor_centavos for p in parcelas) == 100000


def test_retroativa_nao_deixa_faturas_passadas_em_aberto_nem_come_o_limite(sessao, conta, cartao):
    _retroativa(sessao, meio="cartao", cartao_id=cartao.id)

    outubro = cartoes.obter_fatura(sessao, cartao, OUTUBRO)
    resumo = cartoes.resumo(sessao, outubro, HOJE)
    assert (resumo.total, resumo.pago, resumo.restante) == (10000, 10000, 0)
    assert resumo.situacao == Situacao.PAGA

    # Só a parcela da fatura atual (nov) e as duas futuras (dez, jan) contam.
    limite = cartoes.limite(sessao, cartao, HOJE)
    assert (limite.faturas_abertas, limite.parcelas_futuras) == (10000, 20000)
    assert limite.disponivel == cartao.limite_centavos - 30000
    # E o que falta pagar na conta é só o que ainda vai vencer.
    assert cartoes.a_pagar_por_conta(sessao, date(2026, 11, 30), HOJE) == {conta.id: 10000}


def test_retroativa_no_cartao_nao_mexe_no_saldo_e_conta_no_mes_da_parcela(sessao, conta, cartao):
    _retroativa(sessao, meio="cartao", cartao_id=cartao.id)

    assert contas.saldos(sessao, HOJE)[conta.id] == 500000
    abril = lancamentos.listar(sessao, Competencia(2026, 4), lancamentos.Filtros(), HOJE)
    assert lancamentos.totais(abril).saidas == 10000


def test_retroativa_na_conta_historico_sem_descontar_o_saldo(sessao, conta):
    parcelas = _retroativa(sessao, meio="conta", conta_id=conta.id)

    passadas, atual, futuras = parcelas[:7], parcelas[7], parcelas[8:]
    assert all(p.pago and p.retroativo and p.vencimento is None for p in passadas)
    assert not atual.pago and not atual.retroativo and atual.vencimento == date(2026, 11, 1)
    assert [p.vencimento for p in futuras] == [date(2026, 12, 1), date(2027, 1, 1)]
    # Sep e out (depois da data do saldo inicial) não descontam: o saldo informado já as inclui.
    assert contas.saldos(sessao, HOJE)[conta.id] == 500000


def test_retroativa_na_conta_pode_marcar_a_parcela_atual_como_paga(sessao, conta):
    parcelas = _retroativa(sessao, meio="conta", conta_id=conta.id, pago=1)

    assert parcelas[7].pago and not parcelas[7].retroativo
    assert contas.saldos(sessao, HOJE)[conta.id] == 500000 - 10000  # só a parcela de hoje
    assert not parcelas[8].pago


def test_sem_retroativa_as_parcelas_seguem_como_antes(sessao, cartao):
    parcelas = _comprar(sessao, cartao, "2026-11-01", "900,00", repeticao="parcelado", parcelas="3")
    assert [p.fatura.referencia for p in parcelas] == ["2026-11", "2026-12", "2027-01"]
    assert not any(p.retroativo for p in parcelas)


@pytest.mark.parametrize(
    ("campos", "erro"),
    [
        ({"pagas": "10"}, "parcelas_pagas"),  # pagou tudo: não há parcela atual
        ({"pagas": ""}, "parcelas_pagas"),
        ({"pagas": "0"}, "parcelas_pagas"),
        ({"dia": "2026-11-02"}, "data"),  # a parcela atual não pode estar no futuro
    ],
)
def test_retroativa_valida_os_campos(sessao, cartao, campos, erro):
    with pytest.raises(ErroValidacao) as falha:
        _retroativa(sessao, meio="cartao", cartao_id=cartao.id, **campos)
    assert erro in falha.value.erros


def test_retroativo_marcado_sem_parcelar_e_ignorado(sessao, cartao):
    (compra,) = _comprar(sessao, cartao, "2026-11-01", retroativo=1, parcelas_pagas=3)
    assert not compra.retroativo

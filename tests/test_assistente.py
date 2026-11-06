"""Assistente de cadastro inicial (E2-22): saldos de hoje, faturas em aberto e parcelamentos."""

import json
from datetime import date

import pytest
from sqlalchemy import select

from app.db.models import Cartao, Categoria, Conta, Lancamento
from app.domain import assistente, cartoes, contas, lancamentos
from app.domain.competencia import Competencia
from app.domain.formularios import ErroValidacao

HOJE = date(2026, 10, 29)
HTMX = {"HX-Request": "true"}


@pytest.fixture
def conta(sessao) -> Conta:
    """A conta dos testes começa hoje; fixa a data para o teste não depender dela."""
    conta = sessao.scalar(select(Conta))
    conta.data_saldo_inicial = date(2026, 9, 1)
    return conta


@pytest.fixture
def cartao(sessao, conta) -> Cartao:
    return cartoes.salvar(
        sessao,
        {"nome": "Cartão Teste", "limite": "10.000,00", "dia_fechamento": "19",
         "dia_vencimento": "26", "compra_no_fechamento": "fatura_atual",
         "conta_pagamento_id": str(conta.id)},
    )  # fmt: skip


def _mercado(sessao) -> int:
    return sessao.scalar(select(Categoria.id).where(Categoria.nome == "Mercado"))


# ───────── Passo 1: saldos ─────────


def test_saldo_de_hoje_em_conta_sem_lancamentos(sessao, conta):
    assistente.salvar_saldos(sessao, {f"saldo-{conta.id}": "1.234,56"}, HOJE)
    assert contas.saldos(sessao, HOJE)[conta.id] == 123456
    assert conta.data_saldo_inicial == HOJE


def test_saldo_de_hoje_mantem_lancamentos_existentes(sessao, conta):
    contas.salvar(
        sessao,
        {"nome": conta.nome, "tipo": "corrente", "saldo_inicial": "5.000,00",
         "data_saldo_inicial": "2026-09-01"},
        conta,
    )  # fmt: skip
    lancamentos.salvar(
        sessao,
        {"tipo": "saida", "conta_id": str(conta.id), "valor": "100,00", "descricao": "Mercado",
         "data": "2026-10-05", "categoria_id": str(_mercado(sessao)), "pago": "1"},
    )  # fmt: skip
    assistente.salvar_saldos(sessao, {f"saldo-{conta.id}": "-250,00"}, HOJE)
    assert contas.saldos(sessao, HOJE)[conta.id] == -25000
    assert conta.data_saldo_inicial == date(2026, 9, 1)
    assert conta.saldo_inicial_centavos == -15000


def test_saldo_em_branco_nao_mexe_e_valor_invalido_da_erro(sessao, conta):
    antes = (conta.saldo_inicial_centavos, conta.data_saldo_inicial)
    assert assistente.salvar_saldos(sessao, {f"saldo-{conta.id}": ""}, HOJE) == []
    assert (conta.saldo_inicial_centavos, conta.data_saldo_inicial) == antes
    with pytest.raises(ErroValidacao) as erro:
        assistente.salvar_saldos(sessao, {f"saldo-{conta.id}": "abc"}, HOJE)
    assert f"saldo-{conta.id}" in erro.value.erros


# ───────── Passo 2: faturas em aberto ─────────


def test_faturas_em_aberto_entre_fechamento_e_vencimento(sessao, cartao):
    entre = assistente.faturas_em_aberto(sessao, cartao, date(2026, 10, 22))
    assert [f.referencia for f in entre] == ["2026-10", "2026-11"]
    depois = assistente.faturas_em_aberto(sessao, cartao, HOJE)
    assert [f.referencia for f in depois] == ["2026-11"]


def test_total_da_fatura_cria_um_unico_ajuste(sessao, conta, cartao):
    lancamentos.salvar(
        sessao,
        {"tipo": "saida", "meio": "cartao", "cartao_id": str(cartao.id), "valor": "100,00",
         "descricao": "Já lançada", "data": "2026-10-25", "categoria_id": str(_mercado(sessao))},
    )  # fmt: skip
    (fatura,) = assistente.faturas_em_aberto(sessao, cartao, HOJE)
    saldo_antes = contas.saldos(sessao, HOJE)[conta.id]

    assert assistente.definir_total_fatura(sessao, fatura, 150000, HOJE) is None
    assert assistente.definir_total_fatura(sessao, fatura, 150000, HOJE) is None  # idempotente
    ajuste = assistente.ajuste_da_fatura(sessao, fatura)
    assert ajuste.valor_centavos == 140000 and ajuste.descricao == assistente.DESCRICAO_AJUSTE
    assert ajuste.categoria.nome == "Outros gastos" and ajuste.data == HOJE
    assert cartoes.resumo(sessao, fatura, HOJE).total == 150000
    assert contas.saldos(sessao, HOJE)[conta.id] == saldo_antes  # só a fatura muda

    assert assistente.definir_total_fatura(sessao, fatura, 10000, HOJE) is None
    assert assistente.ajuste_da_fatura(sessao, fatura) is None
    erro = assistente.definir_total_fatura(sessao, fatura, 5000, HOJE)
    assert "Já há R$ 100,00 lançados" in erro


def test_ajuste_da_fatura_fechada_fica_no_ciclo_dela(sessao, cartao):
    fechada, _ = assistente.faturas_em_aberto(sessao, cartao, date(2026, 10, 22))
    assistente.definir_total_fatura(sessao, fechada, 80000, date(2026, 10, 22))
    ajuste = assistente.ajuste_da_fatura(sessao, fechada)
    assert ajuste.data == date(2026, 10, 19) and ajuste.competencia == "2026-10"
    # A data está no ciclo da fatura: se o cartão for editado, o ajuste continua nela.
    assert cartoes.fatura_da_compra(sessao, cartao, ajuste.data).id == fechada.id


def test_salvar_faturas_por_campo(sessao, cartao):
    (fatura,) = assistente.faturas_em_aberto(sessao, cartao, HOJE)
    assistente.salvar_faturas(sessao, {f"fatura-{fatura.id}": "2.000,00"}, HOJE)
    assert cartoes.resumo(sessao, fatura, HOJE).total == 200000
    with pytest.raises(ErroValidacao) as erro:
        assistente.salvar_faturas(sessao, {f"fatura-{fatura.id}": "-1,00"}, HOJE)
    assert f"fatura-{fatura.id}" in erro.value.erros


# ───────── Passo 2: parcelamentos em andamento ─────────


def test_parcelamento_gera_so_as_parcelas_seguintes(sessao, cartao):
    criadas = assistente.adicionar_parcelamento(
        sessao,
        cartao,
        {"descricao": "Notebook", "valor": "250,00", "parcela_atual": "3", "parcelas": "5",
         "categoria_id": str(_mercado(sessao))},
        HOJE,
    )  # fmt: skip
    assert [p.parcela_n for p in criadas] == [4, 5]
    assert [p.fatura.referencia for p in criadas] == ["2026-12", "2027-01"]
    assert all(p.valor_centavos == 25000 and p.parcela_total == 5 for p in criadas)
    assert cartoes.limite(sessao, cartao, HOJE).parcelas_futuras == 50000

    (andamento,) = assistente.parcelamentos_em_andamento(sessao, cartao, HOJE)
    assert (andamento.descricao, andamento.restantes, andamento.proxima.parcela_n) == (
        "Notebook", 2, 4,
    )  # fmt: skip


def test_parcelamento_sem_parcelas_restantes(sessao, cartao):
    with pytest.raises(ErroValidacao) as erro:
        assistente.adicionar_parcelamento(
            sessao,
            cartao,
            {"descricao": "X", "valor": "10,00", "parcela_atual": "5", "parcelas": "5",
             "categoria_id": str(_mercado(sessao))},
            HOJE,
        )  # fmt: skip
    assert "parcela_atual" in erro.value.erros


def test_parcela_cai_na_fatura_certa_em_mes_curto(sessao, cartao):
    """Fechamento 30 e hoje 31/01: 31/01 + 1 mês = 28/02, que ainda é a fatura atual."""
    cartao.dia_fechamento, cartao.dia_vencimento = 30, 10
    hoje = date(2027, 1, 31)
    atual = Competencia.de_texto(cartoes.fatura_atual(sessao, cartao, hoje).referencia)
    (parcela,) = assistente.adicionar_parcelamento(
        sessao,
        cartao,
        {"descricao": "TV", "valor": "100,00", "parcela_atual": "1", "parcelas": "2",
         "categoria_id": str(_mercado(sessao))},
        hoje,
    )  # fmt: skip
    assert parcela.fatura.referencia == str(atual.proxima())
    assert cartoes.referencia_da_compra(cartao, parcela.data) == atual.proxima()


def test_pendente_ate_concluir(sessao):
    assert assistente.pendente(sessao)
    assistente.encerrar(sessao)
    assert not assistente.pendente(sessao)


# ───────── Telas ─────────


def _eventos(resposta) -> dict:
    return json.loads(resposta.headers.get("HX-Trigger", "{}"))


def test_convite_na_visao_geral_e_dispensar(cliente):
    assert "Comece pelo cadastro inicial" in cliente.get("/").text
    resposta = cliente.post("/assistente/dispensar", headers=HTMX)
    assert resposta.status_code == 200 and "toast" in _eventos(resposta)
    assert "Comece pelo cadastro inicial" not in cliente.get("/").text
    assert 'href="/assistente"' in cliente.get("/").text  # continua na busca


def test_passo_contas(cliente, sessao, conta):
    inicio = cliente.get("/assistente", follow_redirects=False)
    assert inicio.status_code == 303 and inicio.headers["location"] == "/assistente/contas"
    tela = cliente.get("/assistente/contas").text
    assert "Quanto há em cada conta hoje?" in tela and f'name="saldo-{conta.id}"' in tela

    erro = cliente.post("/assistente/contas", data={f"saldo-{conta.id}": "x"})
    assert erro.status_code == 422 and "Valor inválido" in erro.text
    ok = cliente.post(
        "/assistente/contas", data={f"saldo-{conta.id}": "3.210,00"}, follow_redirects=False
    )
    assert ok.status_code == 303 and ok.headers["location"] == "/assistente/cartoes"
    assert "R$ 3.210,00" in cliente.get("/contas").text


def test_passo_cartoes_sem_cartao(cliente):
    assert "Nenhum cartão cadastrado" in cliente.get("/assistente/cartoes").text


def test_passo_cartoes_fatura_e_parcelamento(cliente, sessao, cartao):
    sessao.commit()
    tela = cliente.get("/assistente/cartoes").text
    assert "Cartão Teste" in tela and "Fatura que vence em" in tela
    fatura_id = int(tela.split('name="fatura-')[1].split('"')[0])

    erro = cliente.post("/assistente/cartoes", data={f"fatura-{fatura_id}": "-5,00"})
    assert erro.status_code == 422 and "não pode ser negativo" in erro.text
    ok = cliente.post(
        "/assistente/cartoes", data={f"fatura-{fatura_id}": "1.000,00"}, follow_redirects=False
    )
    assert ok.status_code == 303 and ok.headers["location"] == "/assistente/carteira"
    assert 'value="1.000,00"' in cliente.get("/assistente/cartoes").text

    formulario = cliente.get(f"/assistente/cartoes/{cartao.id}/parcelamentos/novo", headers=HTMX)
    assert "Parcela na fatura atual" in formulario.text
    invalido = cliente.post(
        f"/assistente/cartoes/{cartao.id}/parcelamentos", data={"descricao": ""}, headers=HTMX
    )
    assert invalido.status_code == 422
    criado = cliente.post(
        f"/assistente/cartoes/{cartao.id}/parcelamentos",
        data={"descricao": "Celular", "valor": "300,00", "parcela_atual": "2", "parcelas": "4",
              "categoria_id": _mercado(sessao)},
        headers=HTMX,
    )  # fmt: skip
    assert criado.status_code == 204
    eventos = _eventos(criado)
    assert eventos["parcelamentos"] and "2 parcela(s)" in eventos["toast"]["mensagem"]
    tela = cliente.get("/assistente/cartoes").text
    assert "Celular" in tela and "próxima 3/4" in tela

    proxima = sessao.scalar(select(Lancamento).where(Lancamento.parcela_n == 3))
    excluido = cliente.delete(f"/assistente/parcelamentos/{proxima.id}", headers=HTMX)
    assert excluido.status_code == 204 and "desfazer" in _eventos(excluido)["toast"]
    assert "Celular" not in cliente.get("/assistente/cartoes").text


def test_passo_carteira_registra_posicao_inicial(cliente):
    tela = cliente.get("/assistente/carteira").text
    assert "Quais investimentos você já tem?" in tela and 'name="linhas"' in tela
    erro = cliente.post("/assistente/carteira", data={"data": "2026-10-01", "linhas": "ITSA4;x;1"})
    assert erro.status_code == 422 and "Linha 1" in erro.text
    ok = cliente.post(
        "/assistente/carteira",
        data={"data": "2026-10-01", "linhas": "ITSA4; 100; 9,50"},
        follow_redirects=False,
    )
    assert ok.status_code == 303 and ok.headers["location"] == "/carteira"
    assert "ITSA4" in cliente.get("/carteira").text
    assert "Comece pelo cadastro inicial" not in cliente.get("/").text


def test_concluir(cliente):
    assert "Concluir sem cadastrar investimentos" in cliente.get("/assistente/carteira").text
    resposta = cliente.post("/assistente/concluir", follow_redirects=False)
    assert resposta.status_code == 303 and resposta.headers["location"] == "/"
    assert "Comece pelo cadastro inicial" not in cliente.get("/").text

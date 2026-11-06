"""Telas do cartão: cadastro, compra pelo drawer, fatura do mês, pagamento e estorno."""

import json

from sqlalchemy import select

from app.db.models import Cartao, Categoria, Conta, Fatura, Lancamento
from app.domain.competencia import Competencia
from app.domain.datas import hoje, somar_meses

HTMX = {"HX-Request": "true"}


def _eventos(resposta) -> dict:
    return json.loads(resposta.headers.get("HX-Trigger", "{}"))


def _cadastrar(cliente, sessao) -> Cartao:
    conta = sessao.scalar(select(Conta))
    resposta = cliente.post(
        "/cartoes",
        data={"nome": "Cartão Teste", "limite": "10.000,00", "dia_fechamento": "19",
              "dia_vencimento": "26", "compra_no_fechamento": "fatura_atual",
              "conta_pagamento_id": conta.id},
        headers=HTMX,
    )  # fmt: skip
    assert resposta.status_code == 204, resposta.text
    return sessao.scalar(select(Cartao))


def _comprar(cliente, sessao, cartao, **campos):
    mercado = sessao.scalar(select(Categoria.id).where(Categoria.nome == "Mercado"))
    dados = {"tipo": "saida", "meio": "cartao", "cartao_id": cartao.id, "valor": "120,00",
             "descricao": "Supermercado", "data": hoje().isoformat(), "categoria_id": mercado,
             "repeticao": "nao"}  # fmt: skip
    dados.update(campos)
    return cliente.post("/lancamentos", data=dados, headers=HTMX)


def test_tela_sem_cartao_tem_estado_vazio(cliente):
    texto = cliente.get("/cartoes").text
    assert "Nenhum cartão cadastrado" in texto


def test_drawer_so_mostra_pagar_com_quando_ha_cartao(cliente, sessao):
    assert "Pagar com" not in cliente.get("/lancamentos/novo", headers=HTMX).text
    _cadastrar(cliente, sessao)
    texto = cliente.get("/lancamentos/novo", headers=HTMX).text
    assert "Pagar com" in texto and "Cartão Teste" in texto


def test_cadastro_com_erros(cliente, sessao):
    resposta = cliente.post(
        "/cartoes", data={"nome": "", "limite": "", "dia_fechamento": "40"}, headers=HTMX
    )
    assert resposta.status_code == 422
    assert "Use um número de 1 a 31." in resposta.text


def test_compra_aparece_na_fatura_do_mes_e_pagamento(cliente, sessao):
    cartao = _cadastrar(cliente, sessao)
    assert _comprar(cliente, sessao, cartao).status_code == 204
    compra = sessao.scalar(select(Lancamento))
    referencia = Competencia.de_texto(compra.fatura.referencia)

    tela = cliente.get(f"/cartoes?mes={referencia}").text
    assert "Supermercado" in tela and "R$ 120,00" in tela and "Pagar fatura" in tela
    lista = cliente.get(f"/lancamentos?mes={Competencia.de_data(compra.data)}").text
    assert "Cartão Teste" in lista and "−R$ 120,00" in lista

    formulario = cliente.get(f"/cartoes/faturas/{compra.fatura_id}/pagar", headers=HTMX).text
    assert 'value="120,00"' in formulario  # sugere o que falta pagar
    conta = sessao.scalar(select(Conta))
    pago = cliente.post(
        f"/cartoes/faturas/{compra.fatura_id}/pagamentos",
        data={"valor": "120,00", "data": hoje().isoformat(), "conta_id": conta.id},
        headers=HTMX,
    )
    assert pago.status_code == 204
    assert _eventos(pago)["toast"]["mensagem"] == "Pagamento de R$ 120,00 registrado."

    pagamento = sessao.scalar(select(Lancamento).where(Lancamento.tipo == "transferencia"))
    lista = cliente.get(f"/lancamentos?mes={Competencia.de_data(hoje())}").text
    assert "Pagamento de fatura" in lista
    assert f"/cartoes/pagamentos/{pagamento.id}/editar" in lista

    estorno = cliente.delete(f"/lancamentos/{pagamento.id}", headers=HTMX)
    assert estorno.status_code == 204 and "desfazer" in _eventos(estorno)["toast"]


def test_compra_retroativa_pelo_drawer(cliente, sessao):
    cartao = _cadastrar(cliente, sessao)
    formulario = cliente.get("/lancamentos/novo", headers=HTMX).text
    assert "Compra retroativa" in formulario and "Parcelas já pagas" in formulario
    assert "Data da parcela atual" in formulario

    resposta = _comprar(
        cliente, sessao, cartao, valor="1.000,00", descricao="Teclado", repeticao="parcelado",
        parcelas="10", retroativo="1", parcelas_pagas="7",
    )  # fmt: skip
    assert resposta.status_code == 204, resposta.text
    assert "7 já pagas" in _eventos(resposta)["toast"]["mensagem"]

    parcelas = sessao.scalars(select(Lancamento).order_by(Lancamento.parcela_n)).all()
    assert [p.retroativo for p in parcelas] == [True] * 7 + [False] * 3
    assert parcelas[7].data == hoje()  # a parcela atual fica na data informada

    # A parcela paga do mês passado aparece como "Já paga"; a deste mês, não.
    mes_passado = Competencia.de_data(somar_meses(hoje(), -1))
    assert "Já paga" in cliente.get(f"/lancamentos?mes={mes_passado}").text
    assert "Já paga" not in cliente.get(f"/lancamentos?mes={Competencia.de_data(hoje())}").text
    # A fatura daquela parcela aparece quitada, sem nenhum pagamento lançado.
    tela = cliente.get(f"/cartoes?mes={parcelas[6].fatura.referencia}").text
    assert "já pago" in tela
    assert not sessao.scalars(select(Lancamento).where(Lancamento.tipo == "transferencia")).all()


def test_compra_retroativa_com_erro_mostra_a_mensagem(cliente, sessao):
    cartao = _cadastrar(cliente, sessao)
    resposta = _comprar(
        cliente, sessao, cartao, repeticao="parcelado", parcelas="3", retroativo="1",
        parcelas_pagas="3",
    )  # fmt: skip
    assert resposta.status_code == 422
    assert "Precisa ser menor que o número de parcelas." in resposta.text
    assert not sessao.scalars(select(Lancamento)).all()


def test_filtro_por_cartao(cliente, sessao):
    cartao = _cadastrar(cliente, sessao)
    _comprar(cliente, sessao, cartao, descricao="No cartão")
    texto = cliente.get(f"/lancamentos?origem=cartao-{cartao.id}").text
    assert "No cartão" in texto
    texto = cliente.get(f"/lancamentos?origem=conta-{sessao.scalar(select(Conta.id))}").text
    assert "No cartão" not in texto


def test_navegar_meses_nao_cria_faturas_vazias(cliente, sessao):
    _cadastrar(cliente, sessao)
    for mes in ("2027-03", "2027-04", "2027-05"):
        assert cliente.get(f"/cartoes?mes={mes}").status_code == 200
    referencias = sessao.scalars(select(Fatura.referencia)).all()
    assert not {"2027-03", "2027-04", "2027-05"} & set(referencias)


def test_arquivar_cartao(cliente, sessao):
    cartao = _cadastrar(cliente, sessao)
    cliente.post(f"/cartoes/{cartao.id}/arquivar", headers=HTMX)
    texto = cliente.get("/cartoes").text
    assert "Arquivados" in texto
    assert "Pagar com" not in cliente.get("/lancamentos/novo", headers=HTMX).text

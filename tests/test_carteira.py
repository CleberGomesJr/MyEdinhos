"""Carteira (E4-12): preço médio, venda parcial, eventos, taxas, renda fixa, proventos, meta."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models import (
    Ativo,
    Conta,
    Cotacao,
    IndiceMacro,
    Operacao,
    ProventoAnunciado,
    TipoProvento,
)
from app.domain import ativos, carteira, contas, operacoes, proventos
from app.domain.desfazer import desfazer
from app.domain.formularios import ErroValidacao

HOJE = date(2026, 10, 31)


def _ativo(sessao, ticker="ITSA4", classe="acao", **extra) -> Ativo:
    return ativos.salvar(sessao, {"ticker": ticker, "nome": ticker, "classe": classe, **extra})


def _op(sessao, ativo, tipo, data, **campos) -> Operacao:
    dados = {"ativo_id": str(ativo.id), "tipo": tipo, "data": data, **campos}
    return operacoes.salvar(sessao, dados)


def _posicao(sessao, ativo, ate=HOJE) -> carteira.Posicao:
    return carteira.posicoes(sessao, ate)[ativo.id]


def test_preco_medio_com_taxas_e_venda_parcial(sessao):
    itsa = _ativo(sessao)
    _op(
        sessao, itsa, "compra", "2026-01-10", quantidade="100", preco_unitario="10,00", taxas="5,00"
    )
    _op(sessao, itsa, "compra", "2026-02-10", quantidade="100", preco_unitario="12,00")
    pos = _posicao(sessao, itsa)
    assert (pos.quantidade, pos.custo_centavos) == (200, 220500)
    assert pos.preco_medio == Decimal("11.025")

    _op(sessao, itsa, "venda", "2026-03-10", quantidade="50", preco_unitario="15,00", taxas="2,50")
    pos = _posicao(sessao, itsa)
    # Venda não muda o preço médio do que sobra; realizado = 747,50 − 50 × 11,025.
    assert pos.quantidade == 150 and pos.preco_medio == Decimal("11.025")
    assert pos.custo_centavos == 165375 and int(pos.realizado) == 74750 - 55125


def test_desdobramento_e_bonificacao(sessao):
    bbas = _ativo(sessao, "BBAS3")
    _op(sessao, bbas, "compra", "2026-01-10", quantidade="100", preco_unitario="40")
    _op(sessao, bbas, "desdobramento", "2026-04-15", quantidade="2")
    pos = _posicao(sessao, bbas)
    assert (pos.quantidade, pos.custo_centavos, pos.preco_medio) == (200, 400000, Decimal(20))
    _op(sessao, bbas, "bonificacao", "2026-05-02", quantidade="20", preco_unitario="5")
    pos = _posicao(sessao, bbas)
    assert (pos.quantidade, pos.custo_centavos) == (220, 410000)
    # Grupamento 10:1.
    _op(sessao, bbas, "desdobramento", "2026-06-01", quantidade="0,1")
    assert _posicao(sessao, bbas).quantidade == 22


def test_nao_vende_mais_do_que_tem_na_data(sessao):
    itsa = _ativo(sessao)
    _op(sessao, itsa, "compra", "2026-05-10", quantidade="10", preco_unitario="10")
    with pytest.raises(ErroValidacao) as erro:
        _op(sessao, itsa, "venda", "2026-05-11", quantidade="11", preco_unitario="10")
    assert "Há só 10" in erro.value.erros["quantidade"]
    with pytest.raises(ErroValidacao) as erro:
        _op(sessao, itsa, "venda", "2026-05-01", quantidade="1", preco_unitario="10")
    assert "data" in erro.value.erros


def test_operacao_com_conta_move_o_saldo(sessao):
    conta = sessao.scalar(select(Conta))
    conta.data_saldo_inicial = date(2026, 1, 1)
    itsa = _ativo(sessao)
    _op(sessao, itsa, "compra", "2026-05-10", quantidade="10", preco_unitario="10", taxas="1,00",
        conta_id=str(conta.id))  # fmt: skip
    _op(sessao, itsa, "dividendo", "2026-06-10", valor="3,50", conta_id=str(conta.id))
    _op(
        sessao,
        itsa,
        "venda",
        "2026-07-10",
        quantidade="5",
        preco_unitario="12",
        conta_id=str(conta.id),
    )
    assert contas.saldos(sessao, HOJE)[conta.id] == -10100 + 350 + 6000


def test_posicao_inicial_em_lote(sessao):
    texto = "ITSA4; 100; 9,50\nMXRF11\t300\t10,12\n\nBTC 0,005 300.000,00\n"
    criadas = ativos.importar_posicao_inicial(sessao, {"data": "2026-10-01", "linhas": texto})
    assert len(criadas) == 3
    mxrf = ativos.por_ticker(sessao, "MXRF11")
    assert mxrf.classe.value == "fii" and ativos.por_ticker(sessao, "BTC").classe.value == "cripto"
    pos = _posicao(sessao, mxrf)
    assert (pos.quantidade, pos.custo_centavos) == (300, 303600)
    cart = carteira.montar(sessao, HOJE)
    assert cart.aportado == 95000 + 303600 + 150000 and len(cart.sem_cotacao) == 3

    with pytest.raises(ErroValidacao) as erro:
        ativos.importar_posicao_inicial(
            sessao, {"data": "2026-10-01", "linhas": "ITSA4;1;1\nXX;abc;1"}
        )
    assert "Linha 2" in erro.value.erros["linhas"]


def test_valor_de_mercado_e_resultado(sessao):
    itsa = _ativo(sessao)
    _op(sessao, itsa, "compra", "2026-01-10", quantidade="100", preco_unitario="10")
    sessao.add(
        Cotacao(
            ativo_id=itsa.id, data=date(2026, 10, 30), fechamento=Decimal("12.45"), fonte="yahoo"
        )
    )
    sessao.flush()
    (linha,) = carteira.montar(sessao, HOJE).linhas
    assert (linha.valor, linha.origem, linha.resultado) == (124500, "cotacao", 24500)
    assert linha.resultado_pct == Decimal("24.5")


def test_renda_fixa_estimada_pelo_cdi(sessao):
    cdb = _ativo(sessao, "CDB-X", "renda_fixa", indexador="cdi", taxa="110")
    _op(sessao, cdb, "compra", "2026-10-27", quantidade="1", preco_unitario="1.000,00")
    for dia in (28, 29, 30):
        sessao.add(
            IndiceMacro(serie="cdi", data=date(2026, 10, dia), valor=Decimal("0.05"), fonte="bcb")
        )
    sessao.flush()
    (linha,) = carteira.montar(sessao, HOJE).linhas
    # 1000 × (1 + 0,05% × 1,1)³ ≈ 1001,65
    assert linha.origem == "estimativa" and linha.valor == 100165


def test_prefixado_sem_indices(sessao):
    pre = _ativo(sessao, "LTN-2029", "tesouro", indexador="prefixado", taxa="12")
    _op(sessao, pre, "compra", "2025-10-31", quantidade="1", preco_unitario="1.000,00")
    (linha,) = carteira.montar(sessao, HOJE).linhas
    assert linha.origem == "estimativa" and linha.valor == 112000


def _anuncio(ativo, tipo, data_com, pagamento, valor) -> ProventoAnunciado:
    return ProventoAnunciado(
        ativo_id=ativo.id, tipo=tipo, data_com=data_com, data_pagamento=pagamento,
        valor_por_unidade=Decimal(valor), fonte="teste",
    )  # fmt: skip


def test_proventos_sugestoes_e_a_receber(sessao):
    itsa = _ativo(sessao)
    _op(sessao, itsa, "compra", "2026-01-10", quantidade="100", preco_unitario="10")
    for data_com, pagamento in (
        (date(2026, 8, 20), date(2026, 9, 1)),
        (date(2026, 10, 20), date(2026, 11, 20)),
    ):
        sessao.add(_anuncio(itsa, TipoProvento.DIVIDENDO, data_com, pagamento, "0.25"))
    sessao.add(_anuncio(itsa, TipoProvento.JCP, date(2025, 12, 1), None, "1"))
    sessao.flush()
    a_receber, sugestoes = proventos.anunciados(sessao, HOJE)
    assert [(p.anuncio.data_com, p.valor) for p in a_receber] == [(date(2026, 10, 20), 2500)]
    assert [(p.anuncio.data_com, p.valor) for p in sugestoes] == [(date(2026, 8, 20), 2500)]

    _op(sessao, itsa, "dividendo", "2026-09-01", valor="25,00")  # lançado: some das sugestões
    assert proventos.anunciados(sessao, HOJE)[1] == []
    assert proventos.por_mes(sessao, proventos.Competencia(2026, 10))[-2][1] == 2500


def test_meta_de_renda_passiva(sessao):
    proventos.definir_meta(sessao, 50000)
    meta = proventos.meta(sessao, proventos_12m=120000, yield_on_cost=Decimal(8))
    assert (meta.media_12m, meta.falta_mensal) == (10000, 40000)
    assert meta.percentual == Decimal(20)
    assert meta.aporte_necessario == 6000000  # 400 × 12 ÷ 8%
    proventos.definir_meta(sessao, None)
    assert proventos.meta(sessao, 0, None) is None


def test_excluir_operacao_e_desfazer(sessao):
    itsa = _ativo(sessao)
    op = _op(sessao, itsa, "compra", "2026-01-10", quantidade="1,5", preco_unitario="10,123")
    registro = operacoes.excluir(sessao, op)
    assert carteira.posicoes(sessao, HOJE) == {}
    assert desfazer(sessao, registro.id)
    assert _posicao(sessao, itsa).quantidade == Decimal("1.5")


def test_quantidades_em_varias_datas_iguais_ao_calculo_completo(sessao):
    itsa = _ativo(sessao)
    _op(sessao, itsa, "compra", "2026-01-10", quantidade="100", preco_unitario="10")
    _op(sessao, itsa, "desdobramento", "2026-03-01", quantidade="2")
    _op(sessao, itsa, "venda", "2026-05-01", quantidade="50", preco_unitario="10")
    dias = [date(2026, 1, 1), date(2026, 2, 1), date(2026, 4, 1), date(2026, 6, 1)]
    quantidades = carteira.quantidades_em(sessao, dias)
    assert [quantidades[d].get(itsa.id) for d in dias] == [None, 100, 200, 150]
    assert carteira.quantidades_em(sessao, []) == {}
    for dia in dias[1:]:
        assert quantidades[dia][itsa.id] == carteira.posicoes(sessao, dia)[itsa.id].quantidade


def test_evolucao_segue_as_operacoes_mes_a_mes(sessao):
    itsa = _ativo(sessao)
    _op(sessao, itsa, "compra", "2026-08-10", quantidade="10", preco_unitario="10")
    _op(sessao, itsa, "compra", "2026-10-10", quantidade="10", preco_unitario="20")
    sessao.add(Cotacao(ativo_id=itsa.id, data=date(2026, 9, 30), fechamento=Decimal(15), fonte="t"))
    sessao.flush()
    pontos = carteira.evolucao(sessao, proventos.Competencia(2026, 10), HOJE)
    assert [(str(m), p, a) for m, p, a in pontos] == [
        ("2026-08", 10000, 10000),  # sem cotação: custo
        ("2026-09", 15000, 10000),
        ("2026-10", 30000, 30000),
    ]


def test_leitura_de_numeros_em_pt_br():
    assert ativos.decimal_de_texto("310.000") == 310000  # milhar
    assert ativos.decimal_de_texto("1.234,56") == Decimal("1234.56")
    assert ativos.decimal_de_texto("0.001") == Decimal("0.001")  # milhar nunca começa com "0."
    assert ativos.decimal_de_texto("9.80") == Decimal("9.80")

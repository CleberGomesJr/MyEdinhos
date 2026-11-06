"""Tesouro Direto (E5-05): leitura do arquivo aberto, preços dos títulos e valor da posição."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models import Cotacao
from app.domain import ativos, carteira, mercado, operacoes
from app.integrations.mercado import tesouro
from app.integrations.mercado.base import ErroFonte

ARQUIVO = """Tipo Titulo;Data Vencimento;Data Base;Taxa Compra Manha;Taxa Venda Manha;PU Compra Manha;PU Venda Manha;PU Base Manha
Tesouro Selic;01/03/2029;30/10/2026;0,06;0,07;20185,67;20170,66;20170,66
Tesouro Selic;01/03/2029;29/10/2026;0,06;0,07;20175,00;20160,00;20160,00
Tesouro Selic;01/03/2029;01/09/2026;0,06;0,07;19500,00;19490,00;19490,00
Tesouro Prefixado;01/01/2032;30/10/2026;15,38;15,50;479,96;477,14;477,14
Tesouro IPCA+ com Juros Semestrais;15/05/2035;30/10/2026;;7,50;;4.100,10;4.100,10
Tesouro Prefixado;01/01/2020;02/01/2020;;;;1000,00;1000,00
Tesouro Selic;linha estranha;30/10/2026;0,06;0,07;1;2;3
Tesouro Selic;01/03/2031;30/10/2026;0,06;0,07;1;0,00;0,00
"""  # noqa: E501
HOJE = date(2026, 11, 3)
AGORA = datetime(2026, 11, 3, 12)


def _precos():
    return tesouro.ler_linhas(ARQUIVO.splitlines(keepends=True))


def _ativo_selic(sessao, ticker="SELIC29", nome="Tesouro Selic 2029"):
    return ativos.salvar(
        sessao,
        {"ticker": ticker, "nome": nome, "classe": "tesouro", "indexador": "selic",
         "taxa": "0,06", "vencimento": "2029-03-01"},
    )  # fmt: skip


def _comprar(sessao, ativo, quantidade, preco, dia="2026-09-01"):
    return operacoes.salvar(
        sessao,
        {"ativo_id": str(ativo.id), "tipo": "compra", "data": dia, "quantidade": quantidade,
         "preco_unitario": preco},
    )  # fmt: skip


def _atualizar(sessao, baixar=_precos, hoje=HOJE):
    relatorio = mercado.Relatorio(em=AGORA)
    mercado.atualizar_tesouro(sessao, hoje, AGORA, relatorio, baixar)
    return relatorio


def test_le_o_arquivo_com_decimal_e_pula_o_que_nao_entende():
    precos = list(_precos())
    assert len(precos) == 6  # sem a linha estranha e sem o título com preço zero
    primeiro = precos[0]
    assert primeiro.tipo == "Tesouro Selic"
    assert primeiro.vencimento == date(2029, 3, 1)
    assert primeiro.data == date(2026, 10, 30)
    assert primeiro.pu_venda == Decimal("20170.66")
    assert primeiro.taxa_compra == Decimal("0.06")
    semestral = next(p for p in precos if "Semestrais" in p.tipo)
    assert semestral.taxa_compra is None
    assert semestral.pu_venda == Decimal("4100.10")


def test_arquivo_em_outro_formato_e_erro_de_fonte():
    with pytest.raises(ErroFonte):
        list(tesouro.ler_linhas(["<html>manutenção</html>\n"]))


@pytest.mark.parametrize(
    ("indexador", "nome", "esperado"),
    [
        ("selic", "Tesouro Selic 2029", "Tesouro Selic"),
        ("ipca", "Tesouro IPCA+ 2035", "Tesouro IPCA+"),
        ("ipca", "Tesouro IPCA+ com Juros Semestrais 2035", "Tesouro IPCA+ com Juros Semestrais"),
        ("prefixado", "Prefixado 2032", "Tesouro Prefixado"),
        ("prefixado", "Prefixado Juros 2033", "Tesouro Prefixado com Juros Semestrais"),
        ("ipca", "Tesouro Educa+ 2040", "Tesouro Educa+"),
        ("ipca", "Tesouro Renda+ 2050", "Tesouro Renda+ Aposentadoria Extra"),
    ],
)
def test_tipo_do_titulo(sessao, indexador, nome, esperado):
    ativo = ativos.salvar(
        sessao,
        {"ticker": "TD", "nome": nome, "classe": "tesouro", "indexador": indexador, "taxa": "6",
         "vencimento": "2035-05-15"},
    )  # fmt: skip
    assert mercado.tipo_do_titulo(ativo) == esperado


def test_guarda_a_lista_do_dia_mais_recente_e_os_precos_do_que_tem(sessao):
    ativo = _ativo_selic(sessao)
    _comprar(sessao, ativo, "1", "19490,00")
    relatorio = _atualizar(sessao)

    assert relatorio.atualizados == ["SELIC29"]
    assert not relatorio.erros
    titulos = mercado.titulos_tesouro(sessao)
    assert {t.tipo for t in titulos} == {
        "Tesouro Selic", "Tesouro Prefixado", "Tesouro IPCA+ com Juros Semestrais",
    }  # fmt: skip
    assert all(t.data_base == date(2026, 10, 30) for t in titulos)  # o vencido de 2020 fica fora

    cotacoes = sessao.scalars(select(Cotacao).where(Cotacao.ativo_id == ativo.id)).all()
    assert {c.data for c in cotacoes} == {
        date(2026, 10, 30), date(2026, 10, 29), date(2026, 9, 1),
    }  # fmt: skip
    assert all(c.fonte == "tesouro" for c in cotacoes)


def test_baixa_no_maximo_uma_vez_por_dia_e_nao_repete_cotacao(sessao):
    ativo = _ativo_selic(sessao)
    _comprar(sessao, ativo, "1", "19490,00")
    _atualizar(sessao)
    chamadas = []

    def baixar():
        chamadas.append(1)
        return _precos()

    _atualizar(sessao, baixar)
    assert not chamadas
    relatorio = _atualizar(sessao, baixar, date(2026, 11, 4))
    assert chamadas
    assert relatorio.cotacoes == 0


def test_sem_titulo_na_carteira_baixa_uma_vez_por_semana(sessao):
    _atualizar(sessao)
    assert mercado.titulos_tesouro(sessao)
    chamadas = []

    def baixar():
        chamadas.append(1)
        return _precos()

    _atualizar(sessao, baixar, date(2026, 11, 8))
    assert not chamadas
    _atualizar(sessao, baixar, date(2026, 11, 10))
    assert chamadas


def test_titulo_que_nao_esta_no_arquivo_vira_erro_no_ativo(sessao):
    ativos.salvar(
        sessao,
        {"ticker": "SELIC40", "nome": "Selic 2040", "classe": "tesouro", "indexador": "selic",
         "taxa": "0", "vencimento": "2040-03-01"},
    )  # fmt: skip
    relatorio = _atualizar(sessao)
    assert "não encontrado" in relatorio.erros["SELIC40"]


def test_falha_no_download_nao_apaga_nada_e_tenta_de_novo(sessao):
    _atualizar(sessao)

    def falha():
        raise ErroFonte("sem conexão")
        yield

    relatorio = _atualizar(sessao, falha, date(2026, 11, 10))
    assert relatorio.erros["Tesouro Direto"] == "sem conexão"
    assert mercado.titulos_tesouro(sessao)
    assert mercado.preferencias.obter(sessao, mercado.TESOURO_BUSCADO) == HOJE.isoformat()


def _valor(sessao, ativo, dia=HOJE):
    posicao = carteira.posicoes(sessao, dia)[ativo.id]
    cotacao = carteira.ultimas_cotacoes(sessao, dia).get(ativo.id)
    return carteira.valor_da_posicao(sessao, posicao, cotacao, dia)


def test_posicao_em_numero_de_titulos_usa_o_preco_do_tesouro(sessao):
    ativo = _ativo_selic(sessao)
    _comprar(sessao, ativo, "0,5", "19490,00")
    _atualizar(sessao)
    valor, origem = _valor(sessao, ativo)
    assert origem == "cotacao"
    assert valor == 1008533  # 0,5 × 20.170,66


def test_quem_lancou_o_total_como_preco_segue_pela_estimativa(sessao):
    """1 × R$ 5.000 não é um título de R$ 20 mil: o preço do Tesouro daria um valor absurdo."""
    ativo = _ativo_selic(sessao)
    _comprar(sessao, ativo, "1", "5000,00")
    _atualizar(sessao)
    valor, origem = _valor(sessao, ativo)
    assert origem != "cotacao"
    assert valor < 2000000


def test_mercado_mostra_os_titulos(cliente, sessao):
    assert "Sem dados do Tesouro ainda" in cliente.get("/mercado").text
    _atualizar(sessao)
    sessao.commit()
    pagina = cliente.get("/mercado").text
    assert "Tesouro Prefixado" in pagina
    assert "01/01/2032" in pagina
    assert "Tesouro Transparente" in pagina

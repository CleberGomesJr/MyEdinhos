"""Leitura das respostas das fontes de mercado (E7-02), com JSON de exemplo: sem internet."""

import io
import json
import urllib.error
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models import IndiceMacro, ProventoAnunciado
from app.domain import ativos, mercado
from app.integrations.mercado import base, bcb, brapi, yahoo
from app.integrations.mercado.base import (
    AtivoNaoEncontrado,
    DadosAtivo,
    ErroFonte,
    Fechamento,
    Provento,
)
from app.routers import mercado as rota_mercado

HOJE = date(2026, 10, 30)


def _segundos(dia: date) -> int:
    return int(datetime(dia.year, dia.month, dia.day, 13, tzinfo=base.FUSO).timestamp())


def _ler(texto: str):
    return json.loads(texto, parse_float=Decimal, parse_int=Decimal)


def _grafico(fechamentos: dict[date, object], eventos=None, preco=None) -> dict:
    dias = list(fechamentos)
    meta = {"regularMarketPrice": preco, "regularMarketTime": _segundos(HOJE)} if preco else {}
    return _ler(json.dumps({
        "chart": {"error": None, "result": [{
            "meta": meta,
            "timestamp": [_segundos(d) for d in dias],
            "indicators": {"quote": [{"close": [fechamentos[d] for d in dias]}]},
            "events": {"dividends": eventos or {}},
        }]}
    }))  # fmt: skip


# ───────── Yahoo ─────────


def test_yahoo_le_precos_inteiros_nulos_e_proventos(monkeypatch):
    pedidos = []

    def responder(url, cabecalhos):
        pedidos.append(url)
        ex = date(2026, 10, 19)  # segunda: a data com é a sexta anterior
        return _grafico(
            {date(2026, 10, 28): 8.979999542236328, date(2026, 10, 29): None, HOJE: 12},
            {"1": {"amount": 0.1, "date": _segundos(ex)}},
        )

    monkeypatch.setattr(yahoo, "obter_json", responder)
    dados = yahoo.Yahoo().buscar("MXRF11", "fii", date(2026, 10, 1), HOJE)
    assert "MXRF11.SA" in pedidos[0]
    assert dados.fechamentos == [
        Fechamento(date(2026, 10, 28), Decimal("8.98")),  # sem o ruído de ponto flutuante
        Fechamento(HOJE, Decimal("12.00")),  # inteiro no JSON não quebra
    ]
    (provento,) = dados.proventos
    assert provento.tipo == "rendimento" and provento.data_com == date(2026, 10, 16)
    assert provento.data_pagamento is None


def test_yahoo_preco_do_dia_ainda_sem_fechamento(monkeypatch):
    monkeypatch.setattr(
        yahoo, "obter_json", lambda *a: _grafico({date(2026, 10, 29): 10}, preco=10.5)
    )
    dados = yahoo.Yahoo().buscar("ITSA4", "acao", date(2026, 10, 1), HOJE)
    assert dados.fechamentos[-1] == Fechamento(HOJE, Decimal("10.50"))
    assert dados.proventos == []


def test_yahoo_cripto_converte_pelo_cambio_do_dia_util(monkeypatch):
    sabado = date(2026, 10, 31)

    def responder(url, cabecalhos):
        if "BTC-USD" in url:
            return _grafico({HOJE: 100000, sabado: 110000})
        assert "BRL%3DX" in url
        return _grafico({HOJE: 5})

    monkeypatch.setattr(yahoo, "obter_json", responder)
    dados = yahoo.Yahoo().buscar("BTC", "cripto", date(2026, 10, 25), sabado)
    assert [f.valor for f in dados.fechamentos] == [Decimal(500000), Decimal(550000)]


def test_yahoo_ativo_inexistente(monkeypatch):
    def nao_achou(url, cabecalhos):
        raise ErroFonte("HTTP 404: symbol may be delisted")

    monkeypatch.setattr(yahoo, "obter_json", nao_achou)
    with pytest.raises(AtivoNaoEncontrado):
        yahoo.Yahoo().buscar("XXXX3", "acao", date(2026, 10, 1), HOJE)
    monkeypatch.setattr(
        yahoo, "obter_json", lambda *a: {"chart": {"result": None, "error": {"x": 1}}}
    )
    with pytest.raises(AtivoNaoEncontrado):
        yahoo.Yahoo().buscar("XXXX3", "acao", date(2026, 10, 1), HOJE)


def test_yahoo_historico_de_proventos_e_ibovespa(monkeypatch):
    evento = {"1": {"amount": 0.5, "date": _segundos(date(2020, 6, 3))}}
    monkeypatch.setattr(yahoo, "obter_json", lambda *a: _grafico({HOJE: 10}, evento))
    fonte = yahoo.Yahoo()
    (provento,) = fonte.proventos_historicos("ITSA4", "acao", date(2016, 1, 1), HOJE)
    assert provento.data_com == date(2020, 6, 2) and provento.tipo == "dividendo"
    assert fonte.proventos_historicos("BTC", "cripto", date(2016, 1, 1), HOJE) == []
    assert fonte.ibovespa(date(2026, 10, 1), HOJE) == [(HOJE, Decimal("10.00"))]


# ───────── brapi ─────────

RESPOSTA_BRAPI = {
    "results": [{
        "regularMarketPrice": 52.71,
        "regularMarketTime": "2026-10-30T18:52:37.000Z",
        "historicalDataPrice": [
            {"date": _segundos(date(2026, 10, 28)), "close": 50.5},
            {"date": _segundos(date(2026, 10, 29)), "close": None},
            {"date": _segundos(date(2026, 9, 1)), "close": 40},
        ],
        "dividendsData": {"cashDividends": [
            {"label": "JCP", "rate": 0.2025, "lastDatePrior": "2026-08-21T03:00:00.000Z",
             "paymentDate": "2026-12-21T03:00:00.000Z"},
            {"label": "DIVIDENDO", "rate": 0.47, "lastDatePrior": "2026-08-21T03:00:00.000Z",
             "paymentDate": None},
            {"label": "BONIFICACAO", "rate": 1, "lastDatePrior": "2026-01-01T03:00:00.000Z"},
        ]},
    }]
}  # fmt: skip


def test_brapi_le_cotacoes_e_proventos_e_conta_requisicoes(monkeypatch):
    pedidos, contagem = [], []

    def responder(url, cabecalhos):
        pedidos.append((url, cabecalhos))
        return _ler(json.dumps(RESPOSTA_BRAPI))

    monkeypatch.setattr(brapi, "obter_json", responder)
    fonte = brapi.Brapi("segredo", lambda: contagem.append(1))
    dados = fonte.buscar("PETR4", "acao", date(2026, 10, 20), HOJE)

    url, cabecalhos = pedidos[0]
    assert "range=1mo" in url and "dividends=true" in url
    assert "segredo" not in url and cabecalhos["Authorization"] == "Bearer segredo"  # fora da URL
    assert contagem == [1]
    assert dados.fechamentos == [
        Fechamento(date(2026, 10, 28), Decimal("50.5")),
        Fechamento(HOJE, Decimal("52.71")),
    ]
    assert dados.proventos == [
        Provento("jcp", date(2026, 8, 21), date(2026, 12, 21), Decimal("0.2025")),
        Provento("dividendo", date(2026, 8, 21), None, Decimal("0.47")),
    ]


def test_brapi_sem_proventos_no_plano_refaz_so_com_cotacoes(monkeypatch):
    pedidos = []

    def responder(url, cabecalhos):
        pedidos.append(url)
        if "dividends" in url:
            raise ErroFonte("HTTP 403: recurso fora do plano")
        return {
            "results": [{"historicalDataPrice": [{"date": _segundos(HOJE), "close": Decimal(9)}]}]
        }

    monkeypatch.setattr(brapi, "obter_json", responder)
    dados = brapi.Brapi("t").buscar("ITSA4", "acao", date(2026, 10, 25), HOJE)
    assert len(pedidos) == 2 and "range=1mo" not in pedidos[0] and "range=5d" in pedidos[1]
    assert dados.fechamentos == [Fechamento(HOJE, Decimal(9))]


def test_brapi_nao_atende_cripto_nem_ativo_inexistente(monkeypatch):
    with pytest.raises(ErroFonte):
        brapi.Brapi("t").buscar("BTC", "cripto", date(2026, 10, 1), HOJE)

    def nao_achou(url, cabecalhos):
        raise ErroFonte("HTTP 404: não encontrado")

    monkeypatch.setattr(brapi, "obter_json", nao_achou)
    with pytest.raises(AtivoNaoEncontrado):
        brapi.Brapi("t").buscar("XXXX3", "acao", date(2026, 10, 1), HOJE)
    monkeypatch.setattr(brapi, "obter_json", lambda *a: {"results": []})
    with pytest.raises(AtivoNaoEncontrado):
        brapi.Brapi("t").buscar("XXXX3", "acao", date(2026, 10, 1), HOJE)


# ───────── Banco Central ─────────


def test_bcb_le_a_serie(monkeypatch):
    pedidos = []

    def responder(url, timeout):
        pedidos.append(url)
        return [{"data": "27/10/2026", "valor": "0.050788"}]

    monkeypatch.setattr(bcb, "obter_json", responder)
    assert bcb.buscar_serie("cdi", date(2026, 10, 1), HOJE) == [
        (date(2026, 10, 27), Decimal("0.050788"))
    ]
    assert "bcdata.sgs.12/" in pedidos[0] and "dataInicial=01/10/2026" in pedidos[0]


def test_bcb_sem_dados_no_periodo_e_formato_estranho(monkeypatch):
    def sem_dados(url, timeout):
        raise ErroFonte("HTTP 404: Value(s) not found")

    monkeypatch.setattr(bcb, "obter_json", sem_dados)
    assert bcb.buscar_serie("ipca", date(2026, 10, 1), HOJE) == []

    def fora(url, timeout):
        raise ErroFonte("sem conexão")

    monkeypatch.setattr(bcb, "obter_json", fora)
    with pytest.raises(ErroFonte):
        bcb.buscar_serie("ipca", date(2026, 10, 1), HOJE)
    monkeypatch.setattr(bcb, "obter_json", lambda url, timeout: {"erro": 1})
    with pytest.raises(ErroFonte):
        bcb.buscar_serie("ipca", date(2026, 10, 1), HOJE)


# ───────── HTTP comum ─────────


class _Resposta(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _erro_http(codigo: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", codigo, "erro", {}, io.BytesIO(b"detalhe"))


def test_obter_json_tenta_de_novo_em_falha_temporaria(monkeypatch):
    respostas = [
        _erro_http(503),
        urllib.error.URLError("rede"),
        _Resposta(b'{"preco": 12, "x": 1.5}'),
    ]
    monkeypatch.setattr(base.time, "sleep", lambda _s: None)

    def abrir(pedido, timeout):
        resposta = respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta

    monkeypatch.setattr(base.urllib.request, "urlopen", abrir)
    assert base.obter_json("http://x") == {"preco": Decimal(12), "x": Decimal("1.5")}
    assert respostas == []


def test_obter_json_desiste_e_nao_insiste_em_erro_definitivo(monkeypatch):
    chamadas = []
    monkeypatch.setattr(base.time, "sleep", lambda _s: None)

    def sempre_fora(pedido, timeout):
        chamadas.append(1)
        raise urllib.error.URLError("sem rede")

    monkeypatch.setattr(base.urllib.request, "urlopen", sempre_fora)
    with pytest.raises(ErroFonte, match="sem conexão"):
        base.obter_json("http://x?token=segredo")
    assert len(chamadas) == 3

    def proibido(pedido, timeout):
        chamadas.append(1)
        raise _erro_http(401)

    chamadas.clear()
    monkeypatch.setattr(base.urllib.request, "urlopen", proibido)
    with pytest.raises(ErroFonte, match="HTTP 401"):
        base.obter_json("http://x")
    assert len(chamadas) == 1

    monkeypatch.setattr(base.urllib.request, "urlopen", lambda p, timeout: _Resposta(b"<html>"))
    with pytest.raises(ErroFonte, match="não é JSON"):
        base.obter_json("http://x")


def test_data_com_pula_fim_de_semana():
    assert base.dia_util_anterior(date(2026, 10, 19)) == date(2026, 10, 16)
    assert base.dia_util_anterior(date(2026, 10, 20)) == date(2026, 10, 19)


# ───────── Atualização com fontes que falham ─────────


class _Quebrada:
    nome = "quebrada"

    def buscar(self, ticker, classe, desde, hoje):
        return {}["formato_mudou"]


class _Boa:
    nome = "boa"

    def buscar(self, ticker, classe, desde, hoje):
        return DadosAtivo(self.nome, [Fechamento(hoje, Decimal(10))])

    def proventos_historicos(self, ticker, classe, desde, hoje):
        return [Provento("dividendo", date(2020, 6, 1), None, Decimal("0.5"))]

    def ibovespa(self, desde, hoje):
        return [(hoje, Decimal(130000))]


def _sem_indices(serie, inicio, fim):
    return []


def test_resposta_fora_do_formato_nao_derruba_a_atualizacao(sessao):
    ativos.salvar(sessao, {"ticker": "AAAA3", "classe": "acao"})
    ativos.salvar(sessao, {"ticker": "BBBB3", "classe": "acao"})
    relatorio = mercado.atualizar(sessao, [_Quebrada()], HOJE, _sem_indices)
    assert (
        set(relatorio.erros) == {"AAAA3", "BBBB3"} and "formato_mudou" in relatorio.erros["AAAA3"]
    )
    relatorio = mercado.atualizar(sessao, [_Quebrada(), _Boa()], HOJE, _sem_indices)
    assert relatorio.atualizados == ["AAAA3", "BBBB3"] and relatorio.erros == {}


def test_indice_com_valor_estranho_vira_erro_no_relatorio(sessao):
    def invalido(serie, inicio, fim):
        raise ValueError("data estranha")

    relatorio = mercado.Relatorio(em=datetime(2026, 10, 30))
    assert mercado.atualizar_indices(sessao, HOJE, invalido, relatorio) == 0
    assert "data estranha" in relatorio.erros["índice cdi"]


def test_historico_de_proventos_e_buscado_uma_vez_e_ibovespa_incremental(sessao):
    ativo = ativos.salvar(sessao, {"ticker": "AAAA3", "classe": "acao"})
    relatorio = mercado.atualizar(sessao, [_Boa()], HOJE, _sem_indices)
    assert relatorio.proventos == 1 and relatorio.indices == 1
    anuncio = sessao.scalar(select(ProventoAnunciado).where(ProventoAnunciado.ativo_id == ativo.id))
    assert anuncio.data_com == date(2020, 6, 1)

    sessao.delete(anuncio)
    sessao.flush()
    amanha = HOJE + timedelta(days=1)
    relatorio = mercado.atualizar(sessao, [_Boa()], amanha, _sem_indices)
    assert relatorio.proventos == 0  # já buscou o histórico desse ativo
    pontos = sessao.scalars(select(IndiceMacro.data).where(IndiceMacro.serie == "ibovespa")).all()
    assert sorted(pontos) == [HOJE, amanha]


def test_atualizacao_nao_roda_em_duplicidade(cliente, monkeypatch):
    chamou = []
    monkeypatch.setattr(mercado, "atualizar", lambda *a: chamou.append(1))
    assert rota_mercado._atualizando.acquire(blocking=False)
    try:
        resposta = cliente.post("/mercado/atualizar", headers={"HX-Request": "true"})
    finally:
        rota_mercado._atualizando.release()
    assert resposta.status_code == 204 and chamou == []
    assert "j\\u00e1 est\\u00e3o" in resposta.headers["hx-trigger"]

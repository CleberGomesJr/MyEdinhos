"""Dados de demonstração (E7-01): nunca tocam no banco real."""

import json
import logging
import sqlite3
from contextlib import closing
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import demo
from app.db.migracoes import aplicar_migracoes
from app.db.session import criar_engine, criar_fabrica_sessao
from app.domain import contas
from app.logs import _HandlerDoApp
from app.main import criar_app

DIA = date(2026, 11, 1)


@pytest.fixture
def cliente_demo(settings):
    configuracao = demo.gerar(settings, DIA)
    app = criar_app(configuracao)
    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as cliente:
        yield cliente, configuracao
    raiz = logging.getLogger()
    for handler in [h for h in raiz.handlers if isinstance(h, _HandlerDoApp)]:
        raiz.removeHandler(handler)
        handler.close()


def test_nao_toca_no_banco_real(settings):
    settings.garantir_pastas()
    aplicar_migracoes(settings.database_url)
    engine = criar_engine(settings.database_url)
    with criar_fabrica_sessao(engine)() as sessao:
        contas.salvar(
            sessao,
            {"nome": "Minha conta real", "tipo": "corrente", "saldo_inicial": "0",
             "data_saldo_inicial": DIA.isoformat()},
        )  # fmt: skip
        sessao.commit()
    engine.dispose()
    configuracao = demo.gerar(settings, DIA)
    assert configuracao.db_path == settings.data_dir / "demo" / "financas.db"
    assert configuracao.db_path != settings.db_path and configuracao.modo_demo
    assert configuracao.brapi_token is None and configuracao.porta == settings.porta + 1
    with closing(sqlite3.connect(settings.db_path)) as conexao:
        assert conexao.execute("SELECT nome FROM contas").fetchall() == [("Minha conta real",)]
        assert conexao.execute("SELECT count(*) FROM lancamentos").fetchone() == (0,)


def test_gera_12_meses_e_e_repetivel(settings):
    def contagens(config):
        with closing(sqlite3.connect(config.db_path)) as conexao:
            return conexao.execute(
                "SELECT count(*), sum(valor_centavos), count(DISTINCT competencia) FROM lancamentos"
            ).fetchone()

    primeira = contagens(demo.gerar(settings, DIA))
    segunda = contagens(demo.gerar(settings, DIA))
    assert primeira == segunda
    assert primeira[0] > 120 and primeira[2] == 12


def test_telas_com_dados_de_demonstracao(cliente_demo):
    cliente, _ = cliente_demo
    for caminho in (
        "/",
        "/lancamentos",
        "/contas",
        "/cartoes",
        "/orcamento",
        "/relatorios",
        "/carteira",
        "/proventos",
        "/mercado",
        "/analise",
        "/analise/alocacao",
        "/analise/objetivos",
        "/analise/regras",
        "/analise/snapshot",
        "/config",
    ):
        resposta = cliente.get(caminho)
        assert resposta.status_code == 200, caminho
        assert "Dados fictícios" in resposta.text, caminho
    assert "BANC4" in cliente.get("/carteira").text
    assert "Sem lançamentos nesses meses" not in cliente.get("/?mes=2026-10").text


def test_nao_busca_mercado_em_demonstracao(cliente_demo):
    cliente, _ = cliente_demo
    resposta = cliente.post("/mercado/atualizar", headers={"HX-Request": "true"})
    mensagem = json.loads(resposta.headers["hx-trigger"])["toast"]["mensagem"]
    assert resposta.status_code == 204 and "demonstração" in mensagem
    assert 'hx-trigger="load delay:300ms"' not in cliente.get("/carteira").text

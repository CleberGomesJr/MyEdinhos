"""Proteções do servidor local (E7-08): DNS rebinding, CSRF e cabeçalhos."""

import os

import pytest
from sqlalchemy import select

from app.cli import criar_atalho
from app.config import HOST, RAIZ
from app.db.models import Conta


def test_formulario_comum_mantem_a_origem_no_navegador(cliente):
    """Com `no-referrer` o navegador manda `Origin: null` em <form method=post> e o CSRF recusa."""
    assert cliente.get("/").headers["Referrer-Policy"] == "same-origin"


def test_so_escuta_na_maquina_local():
    assert HOST == "127.0.0.1"


def test_host_de_fora_e_recusado(cliente):
    assert cliente.get("/saude", headers={"Host": "site-malicioso.com"}).status_code == 400
    assert cliente.get("/saude", headers={"Host": "127.0.0.1:8000"}).status_code == 200
    assert cliente.get("/saude", headers={"Host": "localhost:8000"}).status_code == 200


def test_envio_de_outro_site_e_bloqueado(cliente, sessao):
    conta = sessao.scalar(select(Conta))
    url = f"/contas/{conta.id}/arquivar"
    fora = {"Origin": "https://site-malicioso.com", "Host": "127.0.0.1:8000"}
    assert cliente.post(url, headers=fora).status_code == 403
    assert cliente.post(url, headers={"Origin": "null"}).status_code == 403
    assert cliente.post(url, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    outra_porta = {"Origin": "http://127.0.0.1:9999", "Host": "127.0.0.1:8000"}
    assert cliente.post(url, headers=outra_porta).status_code == 403
    sessao.refresh(conta)
    assert conta.ativa  # nada mudou


def test_envio_da_propria_pagina_passa(cliente, sessao):
    conta = sessao.scalar(select(Conta))
    mesma = {"Origin": "http://127.0.0.1:8000", "Host": "127.0.0.1:8000",
             "Sec-Fetch-Site": "same-origin"}  # fmt: skip
    assert cliente.post(f"/contas/{conta.id}/arquivar", headers=mesma).status_code == 204


def test_cabecalhos_de_seguranca(cliente):
    for caminho in ("/", "/static/css/app.css", "/nao-existe"):
        cabecalhos = cliente.get(caminho).headers
        assert "script-src 'self'" in cabecalhos["content-security-policy"]
        assert "frame-ancestors 'none'" in cabecalhos["content-security-policy"]
        assert cabecalhos["x-content-type-options"] == "nosniff"
        assert cabecalhos["x-frame-options"] == "DENY"


def test_token_nao_vai_para_o_git():
    ignorados = (RAIZ / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert {"data/", ".env", "MyEdinhos/"} <= set(ignorados)  # vault com documentação local


def test_nenhuma_pagina_usa_html_sem_escape():
    for arquivo in (RAIZ / "app" / "templates").rglob("*.html"):
        assert "|safe" not in arquivo.read_text(encoding="utf-8"), arquivo


def test_texto_do_usuario_e_escapado_na_tela(cliente, sessao):
    conta = sessao.scalar(select(Conta))
    malicioso = '<script>alert("x")</script>'
    resposta = cliente.post(
        f"/contas/{conta.id}",
        data={"nome": malicioso, "tipo": "corrente", "saldo_inicial": "0,00",
              "data_saldo_inicial": "2026-01-01"},
    )  # fmt: skip
    assert resposta.status_code == 204
    texto = cliente.get("/contas").text
    assert malicioso not in texto and "&lt;script&gt;" in texto


@pytest.mark.skipif(os.name != "nt", reason="atalho só no Windows")
def test_atalho_no_windows(tmp_path):
    caminho = criar_atalho(tmp_path)
    assert caminho == tmp_path / "MyEdinhos.lnk" and caminho.exists()

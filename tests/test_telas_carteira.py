"""Telas da Carteira, Proventos e Mercado (E4, E5). Nada aqui acessa a internet."""

import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models import Ativo, Operacao, ProventoAnunciado, TipoProvento
from app.domain import mercado
from app.domain.datas import hoje
from app.integrations.mercado.base import DadosAtivo, Fechamento

HTMX = {"HX-Request": "true"}


def _eventos(resposta) -> dict:
    return json.loads(resposta.headers.get("HX-Trigger", "{}"))


@pytest.fixture
def itsa(cliente, sessao) -> Ativo:
    resposta = cliente.post(
        "/carteira/ativos",
        data={"ticker": "itsa4", "nome": "Itaúsa", "classe": "acao"},
        headers=HTMX,
    )
    assert resposta.status_code == 204, resposta.text
    return sessao.scalar(select(Ativo))


def test_telas_vazias(cliente):
    assert "Carteira vazia" in cliente.get("/carteira").text
    assert "Nenhum provento lançado" in cliente.get("/proventos").text
    assert "Nenhum ativo de bolsa ou cripto" in cliente.get("/mercado").text


def test_ativo_compra_cotacao_manual_e_exclusao(cliente, sessao, itsa):
    assert itsa.ticker == "ITSA4"
    repetido = cliente.post(
        "/carteira/ativos", data={"ticker": "ITSA4", "classe": "acao"}, headers=HTMX
    )
    assert repetido.status_code == 422 and "Já existe" in repetido.text

    formulario = cliente.get(f"/carteira/operacoes/nova?ativo_id={itsa.id}", headers=HTMX).text
    assert f'<option value="{itsa.id}" selected' in formulario
    dados = {"ativo_id": itsa.id, "tipo": "compra", "data": "2026-01-10", "quantidade": "100",
             "preco_unitario": "9,50", "taxas": "4,90"}  # fmt: skip
    assert cliente.post("/carteira/operacoes", data=dados, headers=HTMX).status_code == 204
    invalido = cliente.post(
        "/carteira/operacoes", data={**dados, "quantidade": "abc"}, headers=HTMX
    )
    assert invalido.status_code == 422 and "Número inválido" in invalido.text

    cotacao = cliente.post(
        f"/carteira/ativos/{itsa.id}/cotacao",
        data={"data": "2026-10-30", "preco": "12,45"},
        headers=HTMX,
    )
    assert cotacao.status_code == 204
    tela = cliente.get("/carteira").text
    assert "ITSA4" in tela and "R$ 1.245,00" in tela and "R$ 954,90" in tela
    assert "R$ 12,45 em 30/10" in tela

    pagina_ativo = cliente.get(f"/carteira/ativos/{itsa.id}").text
    assert "Compra" in pagina_ativo and "taxas R$ 4,90" in pagina_ativo
    op = sessao.scalar(select(Operacao))
    excluida = cliente.delete(f"/carteira/operacoes/{op.id}", headers=HTMX)
    assert excluida.status_code == 204 and "desfazer" in _eventos(excluida)["toast"]


def test_posicao_inicial_pelo_drawer(cliente):
    assert (
        "código; quantidade; preço médio"
        in cliente.get("/carteira/posicao-inicial", headers=HTMX).text
    )
    erro = cliente.post(
        "/carteira/posicao-inicial", data={"data": "2026-10-01", "linhas": "ABC"}, headers=HTMX
    )
    assert erro.status_code == 422 and "Linha 1" in erro.text
    ok = cliente.post(
        "/carteira/posicao-inicial",
        data={"data": "2026-10-01", "linhas": "MXRF11; 300; 10,12\nHGLG11; 10; 160"},
        headers=HTMX,
    )
    assert ok.status_code == 204 and "2 ativo(s)" in _eventos(ok)["toast"]["mensagem"]
    tela = cliente.get("/carteira").text
    assert "MXRF11" in tela and "HGLG11" in tela and "pelo custo" in tela


def test_proventos_meta_sugestao_e_ignorar(cliente, sessao, itsa):
    dia = hoje()
    cliente.post(
        "/carteira/operacoes",
        data={"ativo_id": itsa.id, "tipo": "posicao_inicial", "quantidade": "100",
              "data": (dia - timedelta(days=200)).isoformat(), "preco_unitario": "10"},
        headers=HTMX,
    )  # fmt: skip
    anuncios = [
        ProventoAnunciado(
            ativo_id=itsa.id,
            tipo=TipoProvento.DIVIDENDO,
            data_com=dia - timedelta(days=d),
            data_pagamento=dia - timedelta(days=d - 10),
            valor_por_unidade=Decimal("0.2"),
            fonte="teste",
        )  # fmt: skip
        for d in (90, 60)
    ]
    sessao.add_all(anuncios)
    sessao.commit()

    tela = cliente.get("/proventos").text
    assert "Proventos para confirmar" in tela and "R$ 20,00" in tela
    assert cliente.post("/proventos/meta", data={"meta": "300,00"}, headers=HTMX).status_code == 204

    lancar = cliente.post(
        "/carteira/operacoes",
        data={"ativo_id": itsa.id, "tipo": "dividendo", "valor": "20,00",
              "data": anuncios[0].data_pagamento.isoformat(), "anuncio_id": anuncios[0].id},
        headers=HTMX,
    )  # fmt: skip
    assert lancar.status_code == 204
    sessao.refresh(anuncios[0])
    assert anuncios[0].operacao_id is not None
    ignorar = cliente.post(f"/proventos/anunciados/{anuncios[1].id}/ignorar", headers=HTMX)
    assert ignorar.status_code == 204

    tela = cliente.get("/proventos").text
    assert "Proventos para confirmar" not in tela
    assert "Meta de renda passiva" in tela and "de R$ 300,00/mês" in tela
    assert "Quem mais pagou" in tela and "ITSA4" in tela


def test_mercado_atualizar_e_automatico(cliente, sessao, itsa, monkeypatch):
    class Falsa:
        nome = "teste"

        def buscar(self, ticker, classe, desde, hoje_):
            return DadosAtivo("teste", [Fechamento(hoje_ - timedelta(days=1), Decimal("12")),
                                        Fechamento(hoje_, Decimal("12.6"))])  # fmt: skip

    monkeypatch.setattr(mercado, "fontes_configuradas", lambda *args: [Falsa()])
    monkeypatch.setattr(
        mercado.bcb, "buscar_serie", lambda serie, inicio, fim: [(date(2026, 10, 1), Decimal("1"))]
    )

    assert 'hx-trigger="load delay:300ms"' in cliente.get("/mercado").text  # nunca atualizou
    resposta = cliente.post("/mercado/atualizar", headers=HTMX)
    assert resposta.status_code == 204 and "1 ativo(s)" in _eventos(resposta)["toast"]["mensagem"]
    tela = cliente.get("/mercado").text
    assert "R$ 12,60" in tela and "+5,0%" in tela and "(teste)" in tela
    assert 'hx-trigger="load delay:300ms"' not in tela

    assert cliente.post("/mercado/automatico", data={}, headers=HTMX).status_code == 204
    assert not mercado.status(sessao).automatico

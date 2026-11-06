"""Telas da Análise (E6)."""

import json
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.db.models import Ativo, Conta, Cotacao, Objetivo, ProventoAnunciado, TipoProvento
from app.domain import alocacao, regras
from app.domain.datas import hoje

HTMX = {"HX-Request": "true"}
MODERADO = {f"alvo-{g.value}": str(a) for g, (a, _) in alocacao.SUGESTAO_MODERADO.items()} | {
    f"tolerancia-{g.value}": str(t) for g, (_, t) in alocacao.SUGESTAO_MODERADO.items()
}


def _toast(resposta) -> str:
    return json.loads(resposta.headers["HX-Trigger"])["toast"]["mensagem"]


def _carteira(cliente, sessao):
    cliente.post(
        "/carteira/posicao-inicial",
        data={"data": "2026-05-04", "linhas": "ITSA4; 100; 10\nMXRF11; 100; 10"},
        headers=HTMX,
    )
    for ativo in sessao.scalars(select(Ativo)):
        sessao.add(Cotacao(ativo_id=ativo.id, data=hoje(), fechamento=Decimal("11"), fonte="teste"))
    sessao.commit()


def test_paginas_vazias(cliente):
    visao = cliente.get("/analise").text
    assert "Sem operações na carteira" in visao and "Regras ainda não configuradas" in visao
    assert "Sem alocação-alvo" in cliente.get("/analise/alocacao").text
    objetivos = cliente.get("/analise/objetivos").text
    assert "Sem meta de renda passiva" in objetivos and "Nenhum outro objetivo" in objetivos
    assert "desligada" in cliente.get("/analise/regras").text
    assert "Copiar snapshot" in cliente.get("/analise/snapshot").text


def test_alocacao_metas_e_aporte(cliente, sessao):
    _carteira(cliente, sessao)
    assert (
        "Usar a sugestão do perfil moderado"
        in cliente.get("/analise/alocacao/metas", headers=HTMX).text
    )
    erro = cliente.post(
        "/analise/alocacao/metas", data={**MODERADO, "alvo-fiis": "10"}, headers=HTMX
    )
    assert erro.status_code == 422 and "somam 90%" in erro.text
    assert cliente.post("/analise/alocacao/metas", data=MODERADO, headers=HTMX).status_code == 204

    tela = cliente.get("/analise/alocacao").text
    assert "Ações Brasil" in tela and "Abaixo" in tela and "Acima" in tela
    resultado = cliente.get("/analise/alocacao/aporte?valor=400,00").text
    assert "Renda fixa / Tesouro" in resultado and "R$" in resultado
    assert "Informe um valor" in cliente.get("/analise/alocacao/aporte?valor=0").text


def test_regras_sugestao_e_alertas(cliente, sessao):
    _carteira(cliente, sessao)
    assert cliente.post("/analise/regras/sugestao", headers=HTMX).status_code == 204
    assert regras.obter(sessao).dy_min_fiis == 8
    tela = cliente.get("/analise/regras").text
    assert 'value="5"' in tela and "Varejo, Proteína animal" in tela
    erro = cliente.post("/analise/regras", data={"limite_ativo_pct": "200"}, headers=HTMX)
    assert erro.status_code == 422 and "Use de 0 a 100" in erro.text
    ok = cliente.post(
        "/analise/regras", data={"limite_ativo_pct": "7", "reserva_primeiro": "1"}, headers=HTMX
    )
    assert ok.status_code == 204 and "Regras salvas" in _toast(ok)
    visao = cliente.get("/analise").text
    assert "ITSA4 acima do limite por ativo" in visao and "ITSA4 sem setor" in visao


def test_objetivos_premissas_e_crud(cliente, sessao):
    resposta = cliente.post(
        "/analise/objetivos/premissas",
        data={
            "premissa_retorno_aa": "10",
            "premissa_yield": "7",
            "aporte_mensal": "400,00",
            "reserva_meses": "6",
        },
        headers=HTMX,
    )
    assert resposta.status_code == 204
    criado = cliente.post(
        "/analise/objetivos",
        data={"nome": "Carro", "valor_alvo": "30.000,00", "prazo": "2029-12-01"},
        headers=HTMX,
    )
    assert criado.status_code == 204
    tela = cliente.get("/analise/objetivos").text
    assert "Carro" in tela and "R$ 400,00 por mês" in tela and "Para o prazo" in tela
    objetivo = sessao.scalar(select(Objetivo))
    excluido = cliente.delete(f"/analise/objetivos/{objetivo.id}", headers=HTMX)
    assert (
        excluido.status_code == 204
        and "desfazer" in json.loads(excluido.headers["HX-Trigger"])["toast"]
    )
    assert cliente.post("/analise/objetivos", data={"nome": ""}, headers=HTMX).status_code == 422


def test_snapshot_downloads_e_pergunta(cliente, sessao):
    _carteira(cliente, sessao)
    md = cliente.get("/analise/snapshot.md")
    assert md.headers["content-type"].startswith("text/markdown") and "| ITSA4 |" in md.text
    js = cliente.get("/analise/snapshot.json").json()
    assert js["posicoes"][0]["ticker"] in {"ITSA4", "MXRF11"} and js["data_referencia"]
    pergunta = cliente.get("/analise/pergunta?ticker=taee11&acao=comprar&valor=500,00").text
    assert "comprar TAEE11 (cerca de R$ 500,00)" in pergunta and "Copiar pergunta" in pergunta
    assert "Informe o código" in cliente.get("/analise/pergunta?ticker=").text


def test_reserva_marcada_na_conta_e_dividendos_no_ativo(cliente, sessao):
    conta = sessao.scalar(select(Conta))
    dados = {
        "nome": conta.nome,
        "tipo": "corrente",
        "saldo_inicial": "1.000,00",
        "data_saldo_inicial": "2026-01-01",
        "reserva_emergencia": "1",
    }
    assert cliente.post(f"/contas/{conta.id}", data=dados, headers=HTMX).status_code == 204
    sessao.refresh(conta)
    assert conta.reserva_emergencia
    assert (
        'name="reserva_emergencia" value="1" checked'
        in cliente.get(f"/contas/{conta.id}/editar", headers=HTMX).text
    )

    _carteira(cliente, sessao)
    itsa = sessao.scalar(select(Ativo).where(Ativo.ticker == "ITSA4"))
    for ano in (2023, 2024, 2025):
        sessao.add(
            ProventoAnunciado(
                ativo_id=itsa.id,
                tipo=TipoProvento.DIVIDENDO,
                data_com=date(ano, 6, 1),
                valor_por_unidade=Decimal("0.5"),
                fonte="teste",
            )
        )
    sessao.commit()
    tela = cliente.get(f"/carteira/ativos/{itsa.id}").text
    assert "Anos seguidos pagando" in tela and "sem cortes no histórico" in tela

"""Exportação do mês em CSV (E3-11)."""

import csv
import io

from sqlalchemy import select

from app.db.models import Categoria, Conta
from app.domain import lancamentos
from app.domain.exportacao import csv_lancamentos


def _lancar(sessao, **campos):
    conta = sessao.scalar(select(Conta))
    mercado = sessao.scalar(select(Categoria.id).where(Categoria.nome == "Mercado"))
    dados = {"tipo": "saida", "conta_id": str(conta.id), "valor": "1.234,56",
             "descricao": "Supermercado", "data": "2026-10-10", "categoria_id": str(mercado),
             "pago": "1", **campos}  # fmt: skip
    return lancamentos.salvar(sessao, dados)


def _linhas(texto: str) -> list[list[str]]:
    assert texto.startswith("﻿")
    return list(csv.reader(io.StringIO(texto[1:]), delimiter=";"))


def test_csv_em_formato_brasileiro(sessao):
    itens = _lancar(sessao) + _lancar(sessao, descricao="=SOMA(A1)", valor="10,00", pago="")
    cabecalho, primeira, segunda = _linhas(csv_lancamentos(itens))
    assert cabecalho[:3] == ["Data", "Competência", "Descrição"]
    assert primeira[0] == "10/10/2026" and primeira[7] == "-1234,56" and primeira[8] == "Pago"
    assert primeira[4] == "Mercado" and primeira[5] == "Conta Teste"
    assert segunda[2] == "'=SOMA(A1)" and segunda[8] == "Pendente"  # sem injeção de fórmula


def test_rota_exporta_mes_e_filtros(cliente, sessao):
    _lancar(sessao, descricao="Feira")
    _lancar(sessao, descricao="Padaria", data="2026-09-10")
    sessao.commit()
    resposta = cliente.get("/lancamentos/exportar.csv?mes=2026-10&busca=feira")
    assert resposta.status_code == 200
    assert resposta.headers["content-type"].startswith("text/csv")
    assert 'filename="lancamentos-2026-10.csv"' in resposta.headers["content-disposition"]
    linhas = _linhas(resposta.content.decode("utf-8"))
    assert [linha[2] for linha in linhas[1:]] == ["Feira"]

    pagina = cliente.get("/lancamentos?mes=2026-10&busca=feira").text
    assert "/lancamentos/exportar.csv?" in pagina and "busca=feira" in pagina

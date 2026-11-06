"""Orçamento por categoria (E3-07): limite vigente, gasto, estados e telas."""

import json

import pytest
from sqlalchemy import select

from app.db.models import Categoria, Conta, TipoCategoria
from app.domain import cartoes, lancamentos, orcamentos
from app.domain.competencia import Competencia
from app.domain.datas import hoje
from app.domain.formularios import ErroValidacao

OUT, NOV, DEZ = Competencia(2026, 10), Competencia(2026, 11), Competencia(2026, 12)
HTMX = {"HX-Request": "true"}


def _categoria(sessao, nome: str) -> Categoria:
    return sessao.scalar(select(Categoria).where(Categoria.nome == nome))


def _gastar(sessao, nome: str, valor: str, dia: str = "2026-10-10", **extra):
    conta = sessao.scalar(select(Conta))
    categoria = str(_categoria(sessao, nome).id)
    dados = {"tipo": "saida", "conta_id": str(conta.id), "valor": valor, "descricao": nome,
             "data": dia, "categoria_id": categoria, "pago": "1", **extra}  # fmt: skip
    lancamentos.salvar(sessao, dados)


def _definir(sessao, nome: str, mes: Competencia, limite: str):
    return orcamentos.definir(sessao, _categoria(sessao, nome), mes, {"limite": limite})


def test_limite_vale_do_mes_em_diante(sessao):
    _definir(sessao, "Mercado", OUT, "500,00")
    mercado = _categoria(sessao, "Mercado").id
    assert orcamentos.limites(sessao, Competencia(2026, 9)) == {}
    assert orcamentos.limites(sessao, OUT) == {mercado: 50000}
    assert orcamentos.limites(sessao, DEZ) == {mercado: 50000}

    _definir(sessao, "Mercado", DEZ, "800,00")
    assert orcamentos.limites(sessao, NOV)[mercado] == 50000
    assert orcamentos.limites(sessao, DEZ)[mercado] == 80000

    # Redefinir em outubro vale de outubro em diante: o limite de dezembro some.
    _definir(sessao, "Mercado", OUT, "600,00")
    assert orcamentos.limites(sessao, DEZ)[mercado] == 60000


def test_em_branco_tira_o_limite_a_partir_do_mes(sessao):
    _definir(sessao, "Lazer", OUT, "200,00")
    assert _definir(sessao, "Lazer", NOV, "") is None
    lazer = _categoria(sessao, "Lazer").id
    assert lazer in orcamentos.limites(sessao, OUT)
    assert lazer not in orcamentos.limites(sessao, NOV)


@pytest.mark.parametrize(
    ("gasto", "estado", "restante"),
    [("300,00", "ok", 20000), ("400,00", "atencao", 10000), ("600,00", "critico", -10000)],
)
def test_estados_do_orcamento(sessao, gasto, estado, restante):
    _definir(sessao, "Mercado", OUT, "500,00")
    _gastar(sessao, "Mercado", gasto)
    (linha,) = [ln for ln in orcamentos.listar(sessao, OUT) if ln.limite]
    assert (linha.estado, linha.restante) == (estado, restante)


def test_gasto_soma_subcategorias_e_cartao(sessao):
    moradia = _categoria(sessao, "Moradia")
    sessao.add(Categoria(nome="Luz", tipo=TipoCategoria.SAIDA, pai=moradia, cor_slot=1))
    sessao.flush()
    _definir(sessao, "Moradia", OUT, "1.000,00")
    _gastar(sessao, "Moradia", "700,00")
    _gastar(sessao, "Luz", "150,00")
    conta = sessao.scalar(select(Conta))
    cartao = cartoes.salvar(
        sessao,
        {"nome": "C", "limite": "1.000,00", "dia_fechamento": "19", "dia_vencimento": "26",
         "compra_no_fechamento": "fatura_atual", "conta_pagamento_id": str(conta.id)},
    )  # fmt: skip
    lancamentos.salvar(
        sessao,
        {"tipo": "saida", "meio": "cartao", "cartao_id": str(cartao.id), "valor": "100,00",
         "descricao": "Lâmpadas", "data": "2026-10-12", "categoria_id": str(moradia.id)},
    )  # fmt: skip
    linha = next(ln for ln in orcamentos.listar(sessao, OUT) if ln.categoria.id == moradia.id)
    assert (linha.gasto, linha.estado) == (95000, "atencao")


def test_ordem_e_total(sessao):
    _definir(sessao, "Mercado", OUT, "1.000,00")
    _definir(sessao, "Lazer", OUT, "100,00")
    _gastar(sessao, "Mercado", "200,00")
    _gastar(sessao, "Lazer", "90,00")
    _gastar(sessao, "Restaurantes", "300,00")
    linhas = orcamentos.listar(sessao, OUT)
    assert [ln.categoria.nome for ln in linhas[:3]] == ["Lazer", "Mercado", "Restaurantes"]
    assert all(ln.categoria.pai_id is None for ln in linhas)
    total = orcamentos.total(linhas)
    assert (total.gasto, total.limite) == (29000, 110000)  # Restaurantes não tem limite


def test_so_categoria_principal_de_saida(sessao):
    with pytest.raises(ErroValidacao):
        _definir(sessao, "Salário", OUT, "100,00")


# ───────── Telas ─────────


def test_telas_do_orcamento(cliente, sessao):
    mes = Competencia.de_data(hoje())
    mercado = _categoria(sessao, "Mercado")
    texto = cliente.get(f"/orcamento?mes={mes}").text
    assert "Definir limite" in texto and "Mercado" in texto

    formulario = cliente.get(f"/orcamento/{mercado.id}/editar?mes={mes}", headers=HTMX).text
    assert "Média dos 3 meses anteriores" in formulario and f'value="{mes}"' in formulario

    erro = cliente.post(
        f"/orcamento/{mercado.id}", data={"mes": str(mes), "limite": "x"}, headers=HTMX
    )
    assert erro.status_code == 422 and "Valor inválido" in erro.text
    ok = cliente.post(
        f"/orcamento/{mercado.id}", data={"mes": str(mes), "limite": "800,00"}, headers=HTMX
    )
    assert ok.status_code == 204
    assert "R$ 800,00" in json.loads(ok.headers["HX-Trigger"])["toast"]["mensagem"]

    _gastar(sessao, "Mercado", "700,00", dia=hoje().isoformat())
    sessao.commit()
    texto = cliente.get(f"/orcamento?mes={mes}").text
    assert "Perto do limite" in texto and "88%" in texto
    visao = cliente.get(f"/?mes={mes}").text
    assert "Ver orçamento" in visao and "de R$ 800,00" in visao


def test_visao_geral_sem_limites(cliente):
    assert "Sem limites definidos" in cliente.get("/").text

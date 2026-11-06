"""Competência × caixa (E3-14), resumo anual (E3-10) e categoria × mês (E3-09)."""

import pytest
from sqlalchemy import select

from app.db.models import Categoria, Conta, TipoCategoria
from app.domain import cartoes, contas, lancamentos, relatorios, resumos
from app.domain.competencia import Competencia

OUTUBRO = Competencia(2026, 10)


@pytest.fixture
def conta(sessao) -> Conta:
    conta = sessao.scalar(select(Conta))
    contas.salvar(
        sessao,
        {"nome": conta.nome, "tipo": "corrente", "saldo_inicial": "1.000,00",
         "data_saldo_inicial": "2026-01-01"},
        conta,
    )  # fmt: skip
    return conta


def _cat(sessao, nome):
    return str(sessao.scalar(select(Categoria.id).where(Categoria.nome == nome)))


def _lancar(sessao, conta, tipo, valor, dia, categoria="Mercado", pago="1"):
    return lancamentos.salvar(
        sessao,
        {"tipo": tipo, "conta_id": str(conta.id), "valor": valor, "descricao": categoria,
         "data": dia, "categoria_id": _cat(sessao, categoria), "pago": pago},
    )  # fmt: skip


def test_caixa_conta_o_pagamento_da_fatura_e_nao_a_compra(sessao, conta):
    cartao = cartoes.salvar(
        sessao,
        {"nome": "C", "limite": "5.000,00", "dia_fechamento": "19", "dia_vencimento": "26",
         "compra_no_fechamento": "fatura_atual", "conta_pagamento_id": str(conta.id)},
    )  # fmt: skip
    _lancar(sessao, conta, "entrada", "3.000,00", "2026-10-05", "Salário")
    _lancar(sessao, conta, "saida", "100,00", "2026-10-07")
    _lancar(sessao, conta, "saida", "50,00", "2026-10-30", pago="")  # pendente: fora do caixa
    lancamentos.salvar(
        sessao,
        {"tipo": "saida", "meio": "cartao", "cartao_id": str(cartao.id), "valor": "400,00",
         "descricao": "Compra", "data": "2026-09-25", "categoria_id": _cat(sessao, "Lazer")},
    )  # fmt: skip
    fatura = cartoes.obter_fatura(sessao, cartao, OUTUBRO)
    cartoes.pagar(
        sessao, fatura, {"valor": "400,00", "data": "2026-10-26", "conta_id": str(conta.id)}
    )

    competencia = resumos.resultado(sessao, OUTUBRO)
    caixa = resumos.resultado(sessao, OUTUBRO, caixa=True)
    assert (competencia.entradas, competencia.saidas) == (300000, 15000)
    assert (caixa.entradas, caixa.saidas) == (300000, 50000)  # 100 + fatura 400
    assert resumos.resultado(sessao, Competencia(2026, 9), caixa=True).saidas == 0

    fatias = {f.nome: f.valor for f in resumos.despesas_por_categoria(sessao, OUTUBRO, caixa=True)}
    assert fatias == {resumos.FATURAS: 40000, "Mercado": 10000}
    fluxo = dict(resumos.fluxo(sessao, OUTUBRO, 2, caixa=True))
    assert fluxo[Competencia(2026, 9)].saidas == 0 and fluxo[OUTUBRO].saidas == 50000


def test_resumo_anual(sessao, conta):
    _lancar(sessao, conta, "entrada", "1.000,00", "2026-03-05", "Salário")
    _lancar(sessao, conta, "saida", "200,00", "2026-03-10")
    _lancar(sessao, conta, "entrada", "1.000,00", "2026-04-05", "Salário")
    _lancar(sessao, conta, "saida", "1.400,00", "2026-04-10")
    resumo = relatorios.resumo_anual(sessao, 2026)
    assert len(resumo.meses) == 12 and resumo.meses[0][0] == Competencia(2026, 1)
    assert (resumo.total.entradas, resumo.total.saidas) == (200000, 160000)
    assert resumo.melhor[0] == Competencia(2026, 3) and resumo.pior[0] == Competencia(2026, 4)
    assert resumo.media_saidas == 80000  # só os 2 meses com movimento
    assert relatorios.resumo_anual(sessao, 2025).melhor is None
    ate_marco = relatorios.resumo_anual(sessao, 2026, ate=Competencia(2026, 3))
    assert ate_marco.pior[0] == Competencia(2026, 3)  # abril ainda não "aconteceu"


def test_mapa_de_categorias(sessao, conta):
    moradia = sessao.scalar(select(Categoria).where(Categoria.nome == "Moradia"))
    sessao.add(Categoria(nome="Luz", tipo=TipoCategoria.SAIDA, pai=moradia, cor_slot=1))
    sessao.flush()
    _lancar(sessao, conta, "saida", "1.000,00", "2026-02-10", "Moradia")
    _lancar(sessao, conta, "saida", "150,00", "2026-02-12", "Luz")
    _lancar(sessao, conta, "saida", "300,00", "2026-05-10", "Mercado")
    mapa = relatorios.mapa_categorias(sessao, 2026)
    assert [linha.categoria.nome for linha in mapa.linhas] == ["Moradia", "Mercado"]
    assert mapa.linhas[0].valores[1] == 115000 and mapa.linhas[0].total == 115000
    assert mapa.maior == 115000
    assert (mapa.intensidade(115000), mapa.intensidade(0), mapa.intensidade(1)) == (100, 0, 8)
    assert mapa.totais_por_mes[4] == 30000


def test_telas(cliente, sessao, conta):
    _lancar(sessao, conta, "saida", "1.234,00", "2026-06-10")
    sessao.commit()
    texto = cliente.get("/relatorios?ano=2026").text
    assert "Mês a mês" in texto and "Gastos por categoria e mês" in texto
    assert "1.234" in texto and 'href="/relatorios?ano=2025"' in texto

    caixa = cliente.get("/?visao=caixa&mes=2026-06")
    assert "por caixa" in caixa.text and caixa.cookies.get("visao") == "caixa"
    assert "por caixa" in cliente.get("/relatorios?ano=2026").text  # lembrado no cookie
    assert "por competência" in cliente.get("/?visao=competencia").text

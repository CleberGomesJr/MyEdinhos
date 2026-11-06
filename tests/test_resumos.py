"""Resumos dos dashboards (E3): resultado do mês, fluxo, categorias, saldos, a pagar e cartões."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models import Categoria, Conta, TipoCategoria
from app.domain import cartoes, contas, lancamentos, resumos
from app.domain.competencia import Competencia
from app.domain.datas import hoje as hoje_real

OUTUBRO = Competencia(2026, 10)
HOJE = date(2026, 10, 29)


@pytest.fixture
def conta(sessao) -> Conta:
    conta = sessao.scalar(select(Conta))
    contas.salvar(
        sessao,
        {"nome": conta.nome, "tipo": "corrente", "saldo_inicial": "5.000,00",
         "data_saldo_inicial": "2026-09-01"},
        conta,
    )  # fmt: skip
    return conta


@pytest.fixture
def cartao(sessao, conta):
    return cartoes.salvar(
        sessao,
        {"nome": "Cartão Teste", "limite": "1.000,00", "dia_fechamento": "19",
         "dia_vencimento": "26", "compra_no_fechamento": "fatura_atual",
         "conta_pagamento_id": str(conta.id)},
    )  # fmt: skip


def _nova_conta(sessao, nome: str, tipo: str, saldo: str) -> Conta:
    dados = {"nome": nome, "tipo": tipo, "saldo_inicial": saldo, "data_saldo_inicial": "2026-09-01"}
    return contas.salvar(sessao, dados)


def _categoria(sessao, nome: str) -> int:
    return sessao.scalar(select(Categoria.id).where(Categoria.nome == nome))


def _lancar(sessao, conta, tipo, valor, dia, categoria="Mercado", **extra):
    dados = {"tipo": tipo, "conta_id": str(conta.id), "valor": valor, "descricao": categoria,
             "data": dia, "pago": "1", **{k: str(v) for k, v in extra.items()}}  # fmt: skip
    if tipo != "transferencia":
        dados["categoria_id"] = str(_categoria(sessao, categoria))
    return lancamentos.salvar(sessao, dados)


def _compra_cartao(sessao, cartao, valor, dia, categoria="Mercado"):
    return lancamentos.salvar(
        sessao,
        {"tipo": "saida", "meio": "cartao", "cartao_id": str(cartao.id), "valor": valor,
         "descricao": "Compra", "data": dia, "categoria_id": str(_categoria(sessao, categoria))},
    )  # fmt: skip


# ───────── E3-01 ─────────


def test_resultado_por_competencia_inclui_cartao_e_ignora_transferencias(sessao, conta, cartao):
    poupanca = _nova_conta(sessao, "Poupança", "poupanca", "0")
    _lancar(sessao, conta, "entrada", "1.000,00", "2026-10-05", "Salário")
    _lancar(sessao, conta, "saida", "300,00", "2026-10-06")
    _compra_cartao(sessao, cartao, "200,00", "2026-10-07")
    _lancar(sessao, conta, "transferencia", "100,00", "2026-10-08", conta_destino_id=poupanca.id)
    fatura = cartoes.obter_fatura(sessao, cartao, OUTUBRO)
    cartoes.pagar(
        sessao, fatura, {"valor": "200,00", "data": "2026-10-26", "conta_id": str(conta.id)}
    )

    resultado = resumos.resultado(sessao, OUTUBRO)
    assert (resultado.entradas, resultado.saidas, resultado.saldo) == (100000, 50000, 50000)
    assert resultado.taxa_poupanca == Decimal(50)
    assert resumos.resultado(sessao, Competencia(2026, 9)).taxa_poupanca is None


def test_fluxo_dos_ultimos_12_meses(sessao, conta):
    _lancar(sessao, conta, "saida", "50,00", "2026-09-10")
    _lancar(sessao, conta, "entrada", "80,00", "2026-10-10", "Salário")
    fluxo = resumos.fluxo(sessao, OUTUBRO)
    assert len(fluxo) == 12 and fluxo[0][0] == Competencia(2025, 11) and fluxo[-1][0] == OUTUBRO
    assert fluxo[-2][1].saidas == 5000 and fluxo[-1][1].entradas == 8000


def test_variacao():
    assert resumos.variacao(150, 100) == Decimal(50)
    assert resumos.variacao(-50, -100) == Decimal(50)  # saldo negativo que melhorou
    assert resumos.variacao(100, 0) is None


def test_saldo_total_diario_e_mensal(sessao, conta):
    _lancar(sessao, conta, "saida", "1.000,00", "2026-10-10")
    arquivada = _nova_conta(sessao, "Antiga", "corrente", "999,00")
    contas.definir_ativa(sessao, arquivada, False)

    assert resumos.saldo_total(sessao, HOJE) == 400000
    diario = resumos.saldo_diario(sessao, OUTUBRO, HOJE)
    assert len(diario) == 29 and diario[8][1] == 500000 and diario[9][1] == 400000
    assert resumos.saldo_diario(sessao, Competencia(2026, 11), HOJE) == []

    mensal = resumos.saldo_mensal(sessao, Competencia(2026, 12), HOJE)
    assert mensal[-1] == (OUTUBRO, 400000)  # novembro e dezembro ainda não aconteceram
    assert dict(mensal)[Competencia(2026, 9)] == 500000
    assert mensal[0][0] == Competencia(2026, 9)  # antes do primeiro saldo inicial: fora


def test_saldo_previsto_desconta_pendentes_e_faturas(sessao, conta, cartao):
    lancamentos.salvar(
        sessao,
        {"tipo": "saida", "conta_id": str(conta.id), "valor": "100,00", "descricao": "Luz",
         "data": "2026-11-10", "categoria_id": str(_categoria(sessao, "Moradia"))},
    )  # fmt: skip
    _compra_cartao(sessao, cartao, "300,00", "2026-10-25")  # vence 26/11
    assert resumos.saldo_previsto(sessao, Competencia(2026, 11), HOJE) == 500000 - 10000 - 30000


# ───────── E3-04 ─────────


def test_despesas_por_categoria_somam_na_mae_e_agrupam_outros(sessao, conta):
    moradia = sessao.get(Categoria, _categoria(sessao, "Moradia"))
    aluguel = Categoria(nome="Aluguel", tipo=TipoCategoria.SAIDA, pai=moradia, cor_slot=1)
    extras = [Categoria(nome=f"Extra {n}", tipo=TipoCategoria.SAIDA, cor_slot=n) for n in (1, 2)]
    sessao.add_all([aluguel, *extras])
    sessao.flush()
    valores = {"Moradia": "100,00", "Aluguel": "900,00", "Mercado": "800,00",
               "Assinaturas": "700,00", "Transporte": "600,00", "Saúde": "500,00",
               "Educação": "400,00", "Lazer": "300,00", "Restaurantes": "200,00",
               "Extra 1": "50,00", "Extra 2": "50,00"}  # fmt: skip
    for nome, valor in valores.items():
        _lancar(sessao, conta, "saida", valor, "2026-10-10", nome)

    fatias = resumos.despesas_por_categoria(sessao, OUTUBRO)
    assert [f.nome for f in fatias][:2] == ["Moradia", "Mercado"]
    assert fatias[0].valor == 100000  # Moradia + Aluguel
    assert len(fatias) == 8 and fatias[-1].nome == "Outros" and fatias[-1].slot == 0
    assert fatias[-1].valor == 30000  # Restaurantes + Extra 1 + Extra 2
    assert sum(f.percentual for f in fatias) == Decimal(100)


def test_despesas_sem_saidas(sessao):
    assert resumos.despesas_por_categoria(sessao, OUTUBRO) == []


# ───────── E3-06 / E3-08 / E3-13 ─────────


def test_saldos_por_conta_proporcionais(sessao, conta):
    _nova_conta(sessao, "Carteira", "dinheiro", "-1.250,00")
    itens = resumos.saldos_por_conta(sessao, HOJE)
    assert [(i.conta.nome, i.saldo, i.proporcao) for i in itens] == [
        (conta.nome, 500000, Decimal(100)),
        ("Carteira", -125000, Decimal(25)),
    ]


def test_a_pagar_nos_proximos_7_dias(sessao, conta, cartao):
    def pendente(descricao, dia):
        lancamentos.salvar(
            sessao,
            {"tipo": "saida", "conta_id": str(conta.id), "valor": "10,00", "descricao": descricao,
             "data": dia, "categoria_id": str(_categoria(sessao, "Moradia"))},
        )  # fmt: skip

    pendente("Atrasada", "2026-10-20")
    pendente("Em 3 dias", "2026-11-01")
    pendente("Em 10 dias", "2026-11-08")
    _compra_cartao(sessao, cartao, "100,00", "2026-10-25")  # fatura aberta: não entra
    _compra_cartao(sessao, cartao, "70,00", "2026-10-01")  # fatura fechada que venceu em 26/10

    itens = resumos.a_pagar(sessao, HOJE)
    assert [(i.descricao, i.atrasado) for i in itens] == [
        ("Atrasada", True),
        ("Fatura Cartão Teste", True),
        ("Em 3 dias", False),
    ]
    assert itens[1].valor == 7000 and itens[1].fatura_id


def test_uso_do_limite_dos_cartoes(sessao, cartao):
    _compra_cartao(sessao, cartao, "750,00", "2026-10-25")
    (uso,) = resumos.uso_dos_cartoes(sessao, HOJE)
    assert (uso.usado, uso.percentual, uso.estado) == (75000, Decimal(75), "atencao")
    assert uso.fatura.total == 75000 and uso.fatura.fatura.referencia == "2026-11"


# ───────── Tela ─────────


def test_visao_geral_com_dados(cliente, sessao, conta):
    dia = hoje_real()
    _lancar(sessao, conta, "entrada", "2.000,00", dia.isoformat(), "Salário")
    _lancar(sessao, conta, "saida", "500,00", dia.isoformat(), "Mercado")
    sessao.commit()
    texto = cliente.get(f"/?mes={Competencia.de_data(dia)}").text
    assert "Saldo hoje" in texto and "Taxa de poupança 75,0%" in texto
    assert 'id="grafico-fluxo"' in texto and 'id="grafico-saldo-mensal"' in texto
    assert "Despesas por categoria" in texto and "R$ 500,00" in texto
    assert "Últimos lançamentos" in texto

    futuro = Competencia.de_data(dia + timedelta(days=62))
    assert "Saldo previsto em" in cliente.get(f"/?mes={futuro}").text


def test_visao_geral_vazia(cliente):
    texto = cliente.get("/").text
    assert "Sem lançamentos nesses meses" in texto
    assert "Nada a pagar nos próximos 7 dias" in texto

"""Fase 3 (E6): alocação, reserva, aporte, regras, dividendos, rentabilidade e objetivos."""

import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models import (
    Categoria,
    Conta,
    Cotacao,
    GrupoAlocacao,
    IndiceMacro,
    ProventoAnunciado,
    TipoProvento,
)
from app.domain import (
    alocacao,
    ativos,
    carteira,
    contas,
    dividendos,
    lancamentos,
    objetivos,
    operacoes,
    proventos,
    regras,
    rentabilidade,
    snapshot,
)
from app.domain.formularios import ErroValidacao

HOJE = date(2026, 11, 1)
MODERADO = {f"alvo-{g.value}": str(a) for g, (a, _) in alocacao.SUGESTAO_MODERADO.items()} | {
    f"tolerancia-{g.value}": str(t) for g, (_, t) in alocacao.SUGESTAO_MODERADO.items()
}


@pytest.fixture
def conta(sessao) -> Conta:
    conta = sessao.scalar(select(Conta))
    conta.data_saldo_inicial = date(2026, 1, 1)
    return conta


def _comprar(
    sessao, ticker, classe, quantidade, preco, cotacao=None, setor=None, data="2026-05-04"
):
    ativo = ativos.por_ticker(sessao, ticker) or ativos.salvar(
        sessao, {"ticker": ticker, "classe": classe, "setor": setor or ""}
    )
    operacoes.salvar(
        sessao,
        {
            "ativo_id": str(ativo.id),
            "tipo": "compra",
            "data": data,
            "quantidade": str(quantidade),
            "preco_unitario": str(preco),
        },
    )
    if cotacao:
        sessao.add(
            Cotacao(ativo_id=ativo.id, data=HOJE, fechamento=Decimal(cotacao), fonte="teste")
        )
        sessao.flush()
    return ativo


def _gasto(sessao, conta, valor, dia):
    mercado_id = sessao.scalar(select(Categoria.id).where(Categoria.nome == "Mercado"))
    lancamentos.salvar(
        sessao,
        {
            "tipo": "saida",
            "conta_id": str(conta.id),
            "valor": valor,
            "descricao": "Gasto",
            "data": dia,
            "categoria_id": str(mercado_id),
            "pago": "1",
        },
    )


# ───────── Alocação e reserva (E6-03, E6-09) ─────────


def test_metas_precisam_somar_100(sessao):
    with pytest.raises(ErroValidacao) as erro:
        alocacao.salvar_metas(sessao, {**MODERADO, "alvo-acoes": "30"})
    assert "somam 103%" in erro.value.erros["alvo-renda_fixa"]
    alocacao.salvar_metas(sessao, MODERADO)
    assert alocacao.metas(sessao)[GrupoAlocacao.FIIS].percentual_alvo == 20


def test_alocacao_inclui_conta_reserva_na_renda_fixa(sessao, conta):
    conta.saldo_inicial_centavos, conta.reserva_emergencia = 400000, True
    _comprar(sessao, "ITSA4", "acao", 100, "10", cotacao="12")  # 1.200
    _comprar(sessao, "MXRF11", "fii", 100, "10", cotacao="9")  # 900
    alocacao.salvar_metas(sessao, MODERADO)
    situacao = alocacao.alocacao(sessao, HOJE)
    assert situacao.total == 610000
    rf = situacao.linha(GrupoAlocacao.RENDA_FIXA)
    assert (rf.valor, rf.estado) == (400000, "acima")  # 65,6% × alvo 45% ± 5
    assert situacao.linha(GrupoAlocacao.ACOES).estado == "abaixo"


def test_reserva_pela_despesa_media(sessao, conta):
    for mes in (8, 9, 10):
        _gasto(sessao, conta, "2.000,00", f"2026-{mes:02d}-10")
    _gasto(sessao, conta, "9.999,00", "2026-11-01")  # mês atual não entra na média
    conta.saldo_inicial_centavos, conta.reserva_emergencia = 1500000, True
    reserva = alocacao.reserva(sessao, HOJE)
    assert reserva.despesa_media == 200000 and reserva.alvo == 1200000
    # saldo hoje: 15.000 − 6.000 − 9.999 = −999,00
    assert reserva.atual == -99900 and not reserva.completa and reserva.falta == 1299900


def test_aporte_vai_primeiro_para_a_reserva(sessao, conta):
    _gasto(sessao, conta, "1.000,00", "2026-10-10")  # despesa média 1.000 → alvo 6.000
    conta.saldo_inicial_centavos, conta.reserva_emergencia = 600000, True  # saldo hoje 5.000
    alocacao.salvar_metas(sessao, MODERADO)
    sugestoes = alocacao.sugerir_aporte(sessao, 40000, HOJE)
    assert [(s.destino, s.valor) for s in sugestoes] == [("Reserva de emergência", 40000)]

    sugestoes = alocacao.sugerir_aporte(sessao, 300000, HOJE)
    assert sugestoes[0].valor == 100000 and sum(s.valor for s in sugestoes) == 300000
    destinos = {s.destino for s in sugestoes[1:]}
    assert {"Ações Brasil", "FIIs", "ETFs / BDRs / exterior"} <= destinos

    sem_regra = alocacao.sugerir_aporte(sessao, 300000, HOJE, reserva_primeiro=False)
    assert "Reserva de emergência" not in {s.destino for s in sem_regra}


# ───────── Regras e alertas (E6-06, E6-12) ─────────


def test_regras_ida_e_volta_e_formulario(sessao):
    assert not regras.obter(sessao).configuradas
    regras.gravar(sessao, regras.SUGESTAO_MODERADO)
    lidas = regras.obter(sessao)
    assert lidas.limite_ativo_pct == 5 and lidas.setores_excluidos[0] == "Varejo"
    form = regras.ler_formulario(
        {
            "limite_ativo_pct": "6,5",
            "setores_excluidos": "Varejo, Aéreas",
            "reserva_primeiro": "1",
            "transicao_patrimonio": "20.000,00",
        }
    )
    assert form.limite_ativo_pct == Decimal("6.5") and form.setores_excluidos == [
        "Varejo",
        "Aéreas",
    ]
    assert form.transicao_patrimonio == 2000000
    with pytest.raises(ErroValidacao):
        regras.ler_formulario({"limite_ativo_pct": "150"})


def test_alertas_de_limite_setor_e_transicao(sessao):
    regras.gravar(sessao, regras.SUGESTAO_MODERADO)
    _comprar(sessao, "ITSA4", "acao", 100, "10", cotacao="10", setor="Bancos")
    _comprar(sessao, "BBAS3", "acao", 10, "10", cotacao="10", setor="Bancos")
    _comprar(sessao, "ITUB4", "acao", 10, "10", cotacao="10", setor="Bancos")
    _comprar(sessao, "MGLU3", "acao", 10, "10", cotacao="10", setor="Varejo")
    _comprar(sessao, "BTC", "cripto", Decimal("0.001"), "300000", cotacao="300000")
    titulos = [a.titulo for a in regras.alertas(sessao, HOJE)]
    # Total de R$ 1.600 (< R$ 20 mil): vale a regra de transição (10% por ativo, 2 por setor).
    assert "ITSA4 acima do limite por ativo" in titulos
    assert "Muitas ações de Bancos" in titulos
    assert "MGLU3 está num setor que você excluiu" in titulos
    assert "Cripto acima do teto" in titulos
    assert titulos[0] == "MGLU3 está num setor que você excluiu"  # crítico primeiro


def test_indicadores_e_alertas_de_dividendos(sessao):
    regras.gravar(sessao, regras.SUGESTAO_MODERADO)
    taee = _comprar(sessao, "TAEE11", "acao", 100, "35", cotacao="40", setor="Energia")
    for ano, valor in ((2021, "3"), (2022, "3.2"), (2023, "3.5"), (2024, "2"), (2025, "3.6")):
        sessao.add(
            ProventoAnunciado(
                ativo_id=taee.id,
                tipo=TipoProvento.DIVIDENDO,
                data_com=date(ano, 6, 1),
                valor_por_unidade=Decimal(valor),
                fonte="teste",
            )
        )
    sessao.add(
        ProventoAnunciado(
            ativo_id=taee.id,
            tipo=TipoProvento.DIVIDENDO,
            data_com=date(2026, 6, 1),
            valor_por_unidade=Decimal("1.2"),
            fonte="teste",
        )
    )
    sessao.flush()
    ind = dividendos.indicadores(sessao, [taee], HOJE)[taee.id]
    assert ind.anos_seguidos(2026) == 5 and ind.cortes(2026) == [2024]
    assert ind.frequencia == "anual" and ind.crescimento_anual(2026).quantize(
        Decimal("0.1")
    ) == Decimal("4.7")
    assert ind.variacao_12m.quantize(Decimal(1)) == Decimal(-67)
    titulos = [a.titulo for a in regras.alertas(sessao, HOJE)]
    assert "TAEE11 cortou os proventos" in titulos
    assert "TAEE11 com yield abaixo do seu mínimo" in titulos  # 1,2 ÷ 40 = 3%


# ───────── Rentabilidade (E6-05) ─────────


def test_xirr():
    taxa = rentabilidade.xirr([(date(2025, 1, 1), -100000), (date(2026, 1, 1), 110000)])
    assert taxa.quantize(Decimal("0.0001")) == Decimal("0.1000")
    assert rentabilidade.xirr([(date(2025, 1, 1), -100)]) is None
    # Fluxos na mesma data somam: mesmo resultado, com bem menos conta.
    separados = [(date(2025, 1, 1), -60000), (date(2025, 1, 1), -40000), (date(2026, 1, 1), 110000)]
    assert rentabilidade.xirr(separados).quantize(Decimal("0.0001")) == Decimal("0.1000")


def test_rentabilidade_com_cdi_no_periodo(sessao):
    _comprar(sessao, "ITSA4", "acao", 100, "10", cotacao="11", data="2026-09-02")
    for dia in range(3, 31):
        sessao.add(
            IndiceMacro(serie="cdi", data=date(2026, 9, dia), valor=Decimal("0.05"), fonte="bcb")
        )
    sessao.add(
        IndiceMacro(serie="ibovespa", data=date(2026, 9, 2), valor=Decimal(130000), fonte="t")
    )
    sessao.add(
        IndiceMacro(serie="ibovespa", data=date(2026, 10, 31), valor=Decimal(136500), fonte="t")
    )
    sessao.flush()
    r = rentabilidade.calcular(sessao, HOJE)
    assert not r.anualizado and r.retorno.quantize(Decimal("0.1")) == Decimal("10.0")
    comparacoes = {c.nome: c.retorno for c in r.comparacoes}
    assert comparacoes["Ibovespa"].quantize(Decimal("0.1")) == Decimal("5.0")
    assert comparacoes["CDI"].quantize(Decimal("0.01")) == Decimal("1.41")
    assert comparacoes["IPCA"] is None


# ───────── Objetivos (E6-09) ─────────


def test_projecoes():
    taxa = (Decimal(1) + Decimal("0.1")) ** (Decimal(1) / 12) - 1
    assert objetivos.meses_para(100000, 100000, 0, taxa) == 0
    assert objetivos.meses_para(0, 1200000, 100000, Decimal(0)) == 12
    assert objetivos.meses_para(0, 10**12, 1, taxa) is None
    assert objetivos.aporte_para(0, 1200000, 12, Decimal(0)) == 100000
    assert objetivos.aporte_para(2000000, 1000000, 12, taxa) == 0


def test_renda_passiva_e_objetivo(sessao):
    alocacao.salvar_metas(sessao, MODERADO)
    proventos.definir_meta(sessao, 50000)
    objetivos.salvar_premissas(
        sessao,
        {
            "premissa_retorno_aa": "10",
            "premissa_yield": "7",
            "aporte_mensal": "400,00",
            "reserva_meses": "6",
        },
    )
    cart = carteira.montar(sessao, HOJE)
    rp = objetivos.renda_passiva(sessao, HOJE, cart, 500000)
    assert rp.patrimonio_rv_necessario == 8571428 and rp.parcela_rv == 55
    assert rp.patrimonio_total_necessario == 15584414
    assert 160 <= rp.meses_estimados <= 175  # ~14 anos

    objetivos.salvar(
        sessao, {"nome": "Entrada do apê", "valor_alvo": "50.000,00", "prazo": "2030-11-01"}
    )
    (prog,) = objetivos.progresso(sessao, HOJE, cart, 500000)
    assert prog.aporte == 40000 and prog.aporte_para_prazo > 40000 and prog.no_prazo is False


# ───────── Snapshot (E6-07, E6-08) ─────────


def test_snapshot_e_pergunta(sessao, conta):
    regras.gravar(sessao, regras.SUGESTAO_MODERADO)
    _comprar(sessao, "ITSA4", "acao", 100, "10", cotacao="12", setor="Bancos")
    dados = snapshot.montar(sessao, HOJE)
    texto = snapshot.para_markdown(dados)
    for trecho in (
        "# Snapshot da carteira — 01/11/2026",
        "## Alocação",
        "| ITSA4 | Ações | Bancos | 100 |",
        "R$ 12,00 (01/11, teste)",
        "Setores excluídos: Varejo",
        "## Alertas atuais",
    ):
        assert trecho in texto, trecho
    json.dumps(snapshot.para_json(dados))  # serializável
    pergunta = snapshot.pergunta(dados, "taee11", "comprar", 50000)
    assert pergunta.startswith(
        "Estou pensando em comprar TAEE11 (cerca de R$ 500,00). Ainda não tenho"
    )
    assert "## Posições" in pergunta


def test_conta_e_ativo_marcados_como_reserva(sessao):
    conta = contas.salvar(
        sessao,
        {
            "nome": "Caixinha",
            "tipo": "poupanca",
            "saldo_inicial": "1.000,00",
            "data_saldo_inicial": "2026-10-01",
            "reserva_emergencia": "1",
        },
    )
    assert conta.reserva_emergencia
    ativo = ativos.salvar(
        sessao,
        {
            "ticker": "SELIC-2029",
            "classe": "tesouro",
            "indexador": "selic",
            "reserva_emergencia": "1",
        },
    )
    assert ativo.reserva_emergencia

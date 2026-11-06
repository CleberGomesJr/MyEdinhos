"""Atualização de mercado (E5-06, E5-07, E5-12) com fontes falsas: os testes não usam internet."""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.db.models import Cotacao, IndiceMacro, ProventoAnunciado
from app.domain import ativos, mercado, preferencias
from app.integrations.mercado.base import DadosAtivo, ErroFonte, Fechamento, Provento

HOJE = date(2026, 10, 31)


class FonteFalsa:
    def __init__(self, nome="falsa", falha=False):
        self.nome, self.falha, self.pedidos = nome, falha, []

    def buscar(self, ticker, classe, desde, hoje):
        self.pedidos.append((ticker, desde))
        if self.falha:
            raise ErroFonte("fora do ar")
        dias = [hoje - timedelta(days=n) for n in (2, 1, 0)]
        return DadosAtivo(
            self.nome,
            [Fechamento(d, Decimal("10.00") + i) for i, d in enumerate(dias) if d >= desde],
            [Provento("dividendo", date(2026, 9, 30), date(2026, 10, 15), Decimal("0.1"))],
        )


def _indice_falso(serie, inicio, fim):
    return [(date(2026, 10, 29), Decimal("0.05")), (date(2026, 10, 30), Decimal("0.05"))]


def test_atualiza_com_reserva_e_e_incremental(sessao):
    itsa = ativos.salvar(sessao, {"ticker": "ITSA4", "nome": "Itaúsa", "classe": "acao"})
    ativos.salvar(sessao, {"ticker": "CDB-X", "nome": "CDB", "classe": "renda_fixa"})  # sem cotação
    fora, reserva = FonteFalsa("brapi", falha=True), FonteFalsa("yahoo")

    relatorio = mercado.atualizar(sessao, [fora, reserva], HOJE, _indice_falso)
    assert relatorio.atualizados == ["ITSA4"] and relatorio.fontes == {"ITSA4": "yahoo"}
    assert relatorio.cotacoes == 3 and relatorio.proventos == 1 and relatorio.indices == 8
    assert reserva.pedidos[0][1] == HOJE - timedelta(days=mercado.HISTORICO_INICIAL)

    # Segunda vez: busca a partir da última data e não duplica nada.
    segunda = FonteFalsa("yahoo")
    relatorio = mercado.atualizar(sessao, [segunda], HOJE, _indice_falso)
    assert segunda.pedidos == [("ITSA4", HOJE)] and relatorio.proventos == 0
    assert sessao.scalar(select(func.count()).select_from(Cotacao)) == 3
    assert sessao.scalar(select(func.count()).select_from(ProventoAnunciado)) == 1
    assert sessao.scalar(select(func.count()).select_from(IndiceMacro)) == 8

    status = mercado.status(sessao)
    assert status.fontes == {"ITSA4": "yahoo"} and not status.nunca
    acompanhado = mercado.acompanhados(sessao, HOJE, {itsa.id})[0]
    assert acompanhado.ultima.fechamento == Decimal("12.00")
    assert acompanhado.variacao.quantize(Decimal("0.01")) == Decimal("9.09")
    assert len(acompanhado.pontos_svg().split()) == 3


def test_erro_em_todas_as_fontes_fica_no_status(sessao):
    ativos.salvar(sessao, {"ticker": "XPTO3", "nome": "X", "classe": "acao"})
    mercado.atualizar(sessao, [FonteFalsa(falha=True)], HOJE, _indice_falso)
    assert "fora do ar" in mercado.status(sessao).erros["XPTO3"]


def test_atualizacao_automatica_uma_vez_por_dia(sessao):
    assert not mercado.precisa_atualizar(sessao, HOJE)  # nada para atualizar
    ativos.salvar(sessao, {"ticker": "ITSA4", "nome": "I", "classe": "acao"})
    assert mercado.precisa_atualizar(sessao, HOJE)
    mercado.atualizar(sessao, [FonteFalsa()], HOJE, _indice_falso)
    assert not mercado.precisa_atualizar(sessao, mercado.status(sessao).em.date())
    mercado.definir_automatico(sessao, False)
    assert not mercado.precisa_atualizar(sessao, HOJE + timedelta(days=5))


def test_fontes_e_cota_da_brapi(sessao):
    assert [f.nome for f in mercado.fontes_configuradas(sessao, None, HOJE)] == ["yahoo"]
    fontes = mercado.fontes_configuradas(sessao, "token", HOJE)
    assert [f.nome for f in fontes] == ["brapi", "yahoo"]
    fontes[0].ao_requisitar()
    assert mercado.requisicoes_brapi(sessao, HOJE) == 1
    preferencias.definir(sessao, mercado.chave_requisicoes(HOJE), str(mercado.LIMITE_BRAPI))
    assert [f.nome for f in mercado.fontes_configuradas(sessao, "token", HOJE)] == ["yahoo"]


def test_indices_acumulados(sessao):
    for mes in range(1, 13):
        sessao.add(IndiceMacro(serie="ipca", data=date(2025 + (mes > 10), (mes + 1) % 12 + 1, 1),
                               valor=Decimal("0.5"), fonte="bcb"))  # fmt: skip
    sessao.add(
        IndiceMacro(
            serie="selic_meta", data=date(2026, 10, 30), valor=Decimal("13.75"), fonte="bcb"
        )
    )
    sessao.flush()
    indices = mercado.indices(sessao, HOJE)
    assert indices.selic_meta == Decimal("13.75")
    assert indices.ipca_12m.quantize(Decimal("0.01")) == Decimal("6.17")


def test_historico_longo_vem_da_fonte_sem_limite(sessao):
    ativos.salvar(sessao, {"ticker": "ITSA4", "nome": "I", "classe": "acao"})
    curta, longa = FonteFalsa("brapi"), FonteFalsa("yahoo")
    curta.historico_dias, longa.historico_dias = 90, None
    mercado.atualizar(sessao, [curta, longa], HOJE, _indice_falso)
    assert longa.pedidos and not curta.pedidos  # 1ª busca: 400 dias, só o Yahoo tem
    mercado.atualizar(sessao, [curta, longa], HOJE, _indice_falso)
    assert curta.pedidos == [("ITSA4", HOJE)]  # depois, a principal volta a ser a primeira

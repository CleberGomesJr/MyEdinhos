"""Yahoo Finance (E5-03), reserva da brapi. API não oficial: pode mudar sem aviso.

Funciona sem token. Tickers da B3 com sufixo `.SA`. Cripto não existe em reais no Yahoo
(conferido em 31/10/2026): busca em dólar (`BTC-USD`) e converte pelo câmbio do dia (`BRL=X`).
Proventos vêm só com a data ex e o valor (sem data de pagamento nem tipo): em FIIs viram
"rendimento", nos demais "dividendo"; a data com é o pregão anterior à data ex.
"""

import urllib.parse
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from app.integrations.mercado.base import (
    FUSO,
    AtivoNaoEncontrado,
    DadosAtivo,
    ErroFonte,
    Fechamento,
    Provento,
    data_de_timestamp,
    dia_util_anterior,
    obter_json,
)

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{simbolo}"
NAVEGADOR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
CAMBIO = "BRL=X"  # reais por dólar


def _segundos(dia: date) -> int:
    return int(datetime.combine(dia, time.min, FUSO).timestamp())


def _grafico(simbolo: str, desde: date, hoje: date) -> tuple[dict[date, Decimal], list[dict]]:
    """({data: fechamento}, eventos de dividendos) de um símbolo do Yahoo."""
    parametros = {
        "period1": _segundos(desde),
        "period2": _segundos(hoje + timedelta(days=1)),
        "interval": "1d",
        "events": "div",
    }
    url = URL.format(simbolo=urllib.parse.quote(simbolo)) + "?" + urllib.parse.urlencode(parametros)
    try:
        dados = obter_json(url, NAVEGADOR)
    except ErroFonte as erro:
        if "404" in str(erro):
            raise AtivoNaoEncontrado(simbolo) from erro
        raise
    grafico = dados.get("chart") or {}
    resultados = grafico.get("result") or []
    if grafico.get("error") or not resultados:
        raise AtivoNaoEncontrado(simbolo)
    item = resultados[0]

    fechamentos: dict[date, Decimal] = {}
    cotacoes = ((item.get("indicators") or {}).get("quote") or [{}])[0]
    for momento, valor in zip(
        item.get("timestamp") or [], cotacoes.get("close") or [], strict=False
    ):
        if valor is not None:
            fechamentos[data_de_timestamp(momento)] = valor
    meta = item.get("meta") or {}
    if meta.get("regularMarketPrice") is not None and meta.get("regularMarketTime"):
        fechamentos.setdefault(
            data_de_timestamp(meta["regularMarketTime"]), meta["regularMarketPrice"]
        )
    eventos = list(((item.get("events") or {}).get("dividends") or {}).values())
    return {dia: valor for dia, valor in fechamentos.items() if dia >= desde}, eventos


def _em_reais(precos_usd: dict[date, Decimal], cambio: dict[date, Decimal]) -> dict[date, Decimal]:
    """Converte pelo câmbio do mesmo dia (ou do último dia útil antes; cripto negocia no fim
    de semana e o câmbio não)."""
    dias_cambio = sorted(cambio)
    convertidos = {}
    for dia, valor in precos_usd.items():
        anteriores = [d for d in dias_cambio if d <= dia]
        taxa = (
            cambio[anteriores[-1]]
            if anteriores
            else (cambio[dias_cambio[0]] if dias_cambio else None)
        )
        if taxa is not None:
            convertidos[dia] = valor * taxa
    return convertidos


class Yahoo:
    nome = "yahoo"
    historico_dias = None  # sem limite

    def buscar(self, ticker: str, classe: str, desde: date, hoje: date) -> DadosAtivo:
        if classe == "cripto":
            precos, _ = _grafico(f"{ticker}-USD", desde, hoje)
            cambio, _ = _grafico(CAMBIO, desde - timedelta(days=7), hoje)
            precos, eventos, casas = _em_reais(precos, cambio), [], Decimal("0.00000001")
        else:
            precos, eventos = _grafico(f"{ticker}.SA", desde, hoje)
            casas = Decimal("0.01")  # o Yahoo devolve 8.979999542236328

        saida = DadosAtivo(self.nome)
        saida.fechamentos = [
            Fechamento(dia, valor.quantize(casas)) for dia, valor in sorted(precos.items())
        ]
        tipo = "rendimento" if ticker.endswith("11") else "dividendo"
        for evento in eventos:
            if evento.get("amount"):
                data_com = dia_util_anterior(data_de_timestamp(evento["date"]))
                saida.proventos.append(Provento(tipo, data_com, None, evento["amount"]))
        return saida

    def ibovespa(self, desde: date, hoje: date) -> list[tuple[date, Decimal]]:
        """Pontos do Ibovespa no fechamento (E6-05: comparação de rentabilidade)."""
        pontos, _ = _grafico("^BVSP", desde, hoje)
        return [(dia, valor.quantize(Decimal("0.01"))) for dia, valor in sorted(pontos.items())]

    def proventos_historicos(
        self, ticker: str, classe: str, desde: date, hoje: date
    ) -> list[Provento]:
        """Proventos de muitos anos (E6-11: anos seguidos pagando, cortes). Uma chamada só."""
        if classe == "cripto":
            return []
        dados = self.buscar(ticker, classe, desde, hoje)
        return dados.proventos

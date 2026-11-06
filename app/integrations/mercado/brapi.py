"""brapi.dev (E5-02): cotações e proventos da B3. Precisa de token (`BRAPI_TOKEN` no .env).

Plano gratuito: 15.000 requisições/mês, 1 ticker por chamada, histórico de até 3 meses.
Uma chamada por ativo traz histórico + proventos (`dividends=true`).
"""

import urllib.parse
from collections.abc import Callable
from datetime import date

from app.integrations.mercado.base import (
    AtivoNaoEncontrado,
    DadosAtivo,
    ErroFonte,
    Fechamento,
    Provento,
    data_de_timestamp,
    obter_json,
)

URL = "https://brapi.dev/api/quote/{ticker}"
FAIXAS = (("5d", 5), ("1mo", 30), ("3mo", 90))  # o plano gratuito vai até 3 meses
TIPOS = {"DIVIDENDO": "dividendo", "JCP": "jcp", "RENDIMENTO": "rendimento"}


def _data_iso(texto: str | None) -> date | None:
    return date.fromisoformat(texto[:10]) if texto else None


class Brapi:
    nome = "brapi"
    historico_dias = 90  # plano gratuito

    def __init__(self, token: str, ao_requisitar: Callable[[], None] | None = None):
        self.token = token
        self.ao_requisitar = ao_requisitar or (lambda: None)

    def _consultar(self, ticker: str, faixa: str, proventos: bool) -> dict:
        parametros = {"range": faixa, "interval": "1d"}
        if proventos:
            parametros["dividends"] = "true"
        url = (
            URL.format(ticker=urllib.parse.quote(ticker)) + "?" + urllib.parse.urlencode(parametros)
        )
        self.ao_requisitar()
        return obter_json(url, {"Authorization": f"Bearer {self.token}"})

    def buscar(self, ticker: str, classe: str, desde: date, hoje: date) -> DadosAtivo:
        if classe == "cripto":
            raise ErroFonte("cripto não é buscada na brapi")
        dias = (hoje - desde).days
        faixa = next((nome for nome, limite in FAIXAS if dias <= limite), FAIXAS[-1][0])
        try:
            dados = self._consultar(ticker, faixa, proventos=True)
        except ErroFonte as erro:
            if "404" in str(erro) or "não encontrad" in str(erro).lower():
                raise AtivoNaoEncontrado(ticker) from erro
            # Proventos podem não estar no plano: tenta só as cotações.
            dados = self._consultar(ticker, faixa, proventos=False)
        resultados = dados.get("results") or []
        if not resultados:
            raise AtivoNaoEncontrado(ticker)
        item = resultados[0]

        saida = DadosAtivo(self.nome)
        for linha in item.get("historicalDataPrice") or []:
            if linha.get("close") is not None:
                dia = data_de_timestamp(linha["date"])
                if dia >= desde:
                    saida.fechamentos.append(Fechamento(dia, linha["close"]))
        if item.get("regularMarketPrice") is not None and item.get("regularMarketTime"):
            dia = _data_iso(item["regularMarketTime"])
            if dia and dia >= desde and all(f.data != dia for f in saida.fechamentos):
                saida.fechamentos.append(Fechamento(dia, item["regularMarketPrice"]))

        for linha in (item.get("dividendsData") or {}).get("cashDividends") or []:
            tipo = TIPOS.get(str(linha.get("label", "")).upper())
            data_com = _data_iso(linha.get("lastDatePrior"))
            if tipo and data_com and linha.get("rate"):
                saida.proventos.append(
                    Provento(tipo, data_com, _data_iso(linha.get("paymentDate")), linha["rate"])
                )
        return saida

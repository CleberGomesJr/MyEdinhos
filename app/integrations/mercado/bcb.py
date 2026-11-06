"""Banco Central — SGS (E5-04). API oficial, gratuita, sem token.

Séries conferidas em 31/10/2026: 12 = CDI (% ao dia), 11 = Selic (% ao dia),
433 = IPCA (% no mês, data = 1º dia do mês), 432 = meta Selic (% ao ano).
"""

from datetime import date, datetime
from decimal import Decimal

from app.integrations.mercado.base import ErroFonte, obter_json

SERIES = {"cdi": 12, "selic": 11, "ipca": 433, "selic_meta": 432}
URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados"


def buscar_serie(serie: str, inicio: date, fim: date) -> list[tuple[date, Decimal]]:
    url = URL.format(codigo=SERIES[serie]) + (
        f"?formato=json&dataInicial={inicio:%d/%m/%Y}&dataFinal={fim:%d/%m/%Y}"
    )
    try:
        linhas = obter_json(url, timeout=15)
    except ErroFonte as erro:
        if "404" in str(erro):  # período sem dados (ex.: IPCA do mês ainda não saiu)
            return []
        raise
    if not isinstance(linhas, list):
        raise ErroFonte("formato inesperado do SGS")
    return [
        (datetime.strptime(linha["data"], "%d/%m/%Y").date(), Decimal(str(linha["valor"])))
        for linha in linhas
    ]

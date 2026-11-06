"""Tesouro Direto (E5-05): preços e taxas diários, dados abertos do Tesouro Transparente.

Arquivo CSV único, com todo o histórico (conferido em 03/11/2026):
`Tipo Titulo;Data Vencimento;Data Base;Taxa Compra Manha;Taxa Venda Manha;PU Compra Manha;...`.
Lido em fluxo, linha a linha, sem guardar o arquivo. "PU Venda" é o preço de resgate.
"""

import csv
import urllib.error
import urllib.request
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.integrations.mercado.base import ErroFonte

URL = (
    "https://www.tesourotransparente.gov.br/ckan/dataset/df56aa42-484a-4a59-8184-7676580c81e3"
    "/resource/796d2059-14e9-44e3-80c9-2d9e30b405c1/download/precotaxatesourodireto.csv"
)


@dataclass(frozen=True)
class PrecoTitulo:
    tipo: str
    vencimento: date
    data: date
    taxa_compra: Decimal | None
    taxa_venda: Decimal | None
    pu_compra: Decimal | None
    pu_venda: Decimal


def _data(texto: str) -> date:
    return datetime.strptime(texto.strip(), "%d/%m/%Y").date()


def _numero(texto: str) -> Decimal | None:
    limpo = texto.strip().replace(".", "").replace(",", ".")
    return Decimal(limpo) if limpo else None


def ler_linhas(linhas: Iterable[str]) -> Iterator[PrecoTitulo]:
    leitor = csv.reader(linhas, delimiter=";")
    cabecalho = next(leitor, None)
    if not cabecalho or "tipo titulo" not in cabecalho[0].lower():
        raise ErroFonte("formato inesperado do arquivo do Tesouro")
    for coluna in leitor:
        if len(coluna) < 7:
            continue
        try:
            pu_venda = _numero(coluna[6])
            if pu_venda is None or not pu_venda:
                continue  # título sem preço de resgate nesse dia
            yield PrecoTitulo(
                coluna[0].strip(), _data(coluna[1]), _data(coluna[2]), _numero(coluna[3]),
                _numero(coluna[4]), _numero(coluna[5]), pu_venda,
            )  # fmt: skip
        except (ValueError, InvalidOperation):
            continue  # uma linha estranha não invalida o arquivo


def baixar() -> Iterator[PrecoTitulo]:
    pedido = urllib.request.Request(URL, headers={"User-Agent": "MyEdinhos"})
    try:
        with urllib.request.urlopen(pedido, timeout=60) as resposta:  # noqa: S310
            yield from ler_linhas(linha.decode("utf-8-sig", "replace") for linha in resposta)
    except (urllib.error.URLError, TimeoutError, OSError) as erro:
        raise ErroFonte(f"sem conexão ({erro})") from erro

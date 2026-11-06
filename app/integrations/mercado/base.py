"""Tipos e HTTP comuns às fontes de mercado (E5-01).

Números chegam como `Decimal` (o JSON é lido com `parse_float=Decimal`): nada passa por float.
"""

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("America/Sao_Paulo")
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Fechamento:
    data: date
    valor: Decimal


@dataclass(frozen=True)
class Provento:
    tipo: str  # dividendo, jcp ou rendimento
    data_com: date
    data_pagamento: date | None
    valor_por_unidade: Decimal


@dataclass
class DadosAtivo:
    fonte: str
    fechamentos: list[Fechamento] = field(default_factory=list)
    proventos: list[Provento] = field(default_factory=list)


class ErroFonte(Exception):
    """Falha ao buscar numa fonte; a próxima da cadeia é tentada."""


class AtivoNaoEncontrado(ErroFonte):
    pass


class FonteCotacoes(Protocol):
    nome: str

    def buscar(self, ticker: str, classe: str, desde: date, hoje: date) -> DadosAtivo:
        """Fechamentos de `desde` até hoje e os proventos que a fonte conhecer."""
        ...


def data_de_timestamp(segundos: int | Decimal) -> date:
    return datetime.fromtimestamp(int(segundos), FUSO).date()


def dia_util_anterior(dia: date) -> date:
    """Data com = último pregão antes da data ex (fins de semana; feriados não)."""
    dia -= timedelta(days=1)
    while dia.weekday() >= 5:
        dia -= timedelta(days=1)
    return dia


def obter_json(
    url: str, headers: dict[str, str] | None = None, tentativas: int = 3, timeout: float = 10
) -> Any:
    """GET com tentativas e espera crescente em falha de rede, 429 e 5xx (E5-01)."""
    pedido = urllib.request.Request(url, headers={"Accept": "application/json", **(headers or {})})
    for tentativa in range(1, tentativas + 1):
        try:
            with urllib.request.urlopen(pedido, timeout=timeout) as resposta:  # noqa: S310
                # Decimal também nos inteiros: um preço pode vir como 12 em vez de 12.0.
                texto = resposta.read().decode("utf-8")
                return json.loads(texto, parse_float=Decimal, parse_int=Decimal)
        except urllib.error.HTTPError as erro:
            corpo = erro.read().decode("utf-8", "replace")[:300]
            if erro.code == 429 or erro.code >= 500:
                motivo = f"HTTP {erro.code}"
            else:
                raise ErroFonte(f"HTTP {erro.code}: {corpo}") from erro
        except (urllib.error.URLError, TimeoutError, OSError) as erro:
            motivo = f"sem conexão ({erro})"
        except json.JSONDecodeError as erro:
            raise ErroFonte("resposta não é JSON") from erro
        if tentativa < tentativas:
            time.sleep(0.5 * 2 ** (tentativa - 1))
    log.warning("Falha ao acessar %s: %s", url.split("?")[0], motivo)
    raise ErroFonte(motivo)

"""Formatação e leitura de valores em pt-BR. Dinheiro sempre em centavos inteiros."""

import re
from decimal import ROUND_HALF_EVEN, Decimal

MENOS = "−"  # sinal de menos tipográfico (−)

_VALOR_DIGITADO = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?")


def _milhar_br(numero: str) -> str:
    """'1,234.5' (formato Python) -> '1.234,5'."""
    return numero.translate(str.maketrans(",.", ".,"))


def formatar_brl(centavos: int, sinal: bool = False) -> str:
    """123456 -> 'R$ 1.234,56'; negativos com '−'; `sinal=True` põe '+' nos positivos."""
    reais, resto = divmod(abs(centavos), 100)
    texto = f"R$ {_milhar_br(f'{reais:,}')},{resto:02d}"
    if centavos < 0:
        return MENOS + texto
    if sinal and centavos > 0:
        return "+" + texto
    return texto


def centavos_de_texto(texto: str) -> int:
    """Lê um valor digitado ('R$ 1.234,56', '1234,5', '-10') e devolve centavos.

    Levanta `ValueError` se o texto não for um valor em reais válido.
    """
    limpo = re.sub(r"\s", "", texto.replace("R$", ""))
    negativo = limpo[:1] in ("-", MENOS)
    if negativo:
        limpo = limpo[1:]
    if not _VALOR_DIGITADO.fullmatch(limpo):
        raise ValueError(f"Valor inválido: {texto!r}")
    inteiro, _, fracao = limpo.replace(".", "").partition(",")
    centavos = int(inteiro) * 100 + int(fracao.ljust(2, "0"))
    return -centavos if negativo else centavos


def formatar_percentual(valor: Decimal, casas: int = 1, sinal: bool = False) -> str:
    """Decimal(4.25) -> '4,3%' (valor já em pontos percentuais)."""
    arredondado = valor.quantize(Decimal(1).scaleb(-casas), rounding=ROUND_HALF_EVEN)
    texto = _milhar_br(f"{abs(arredondado):,.{casas}f}") + "%"
    if arredondado < 0:
        return MENOS + texto
    if sinal and arredondado > 0:
        return "+" + texto
    return texto


def formatar_preco(valor: Decimal | None) -> str:
    """Preço unitário: 2 casas (R$ 12,45); abaixo de R$ 1, até 6 casas (R$ 0,000123)."""
    if valor is None:
        return "—"
    casas = 2 if abs(valor) >= 1 or valor == 0 else 6
    texto = f"{abs(valor):,.{casas}f}"
    if casas > 2:
        inteiro, fracao = texto.split(".")
        texto = inteiro + "." + fracao.rstrip("0").ljust(2, "0")
    texto = "R$ " + _milhar_br(texto)
    return MENOS + texto if valor < 0 else texto

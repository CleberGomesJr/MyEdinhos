from decimal import Decimal

import pytest

from app.domain.dinheiro import centavos_de_texto, formatar_brl, formatar_percentual


@pytest.mark.parametrize(
    ("centavos", "esperado"),
    [
        (0, "R$ 0,00"),
        (5, "R$ 0,05"),
        (123456, "R$ 1.234,56"),
        (2500000, "R$ 25.000,00"),
        (123456789012, "R$ 1.234.567.890,12"),
        (-990, "−R$ 9,90"),
    ],
)
def test_formatar_brl(centavos, esperado):
    assert formatar_brl(centavos) == esperado


def test_formatar_brl_com_sinal():
    assert formatar_brl(1000, sinal=True) == "+R$ 10,00"
    assert formatar_brl(0, sinal=True) == "R$ 0,00"
    assert formatar_brl(-1000, sinal=True) == "−R$ 10,00"


@pytest.mark.parametrize(
    ("texto", "centavos"),
    [
        ("1.234,56", 123456),
        ("R$ 1.234,56", 123456),
        ("R$ 25.000", 2500000),
        ("1234,5", 123450),
        ("1234", 123400),
        ("0,1", 10),
        ("-10,00", -1000),
        ("−10,00", -1000),
        (" 7 ", 700),
    ],
)
def test_centavos_de_texto(texto, centavos):
    assert centavos_de_texto(texto) == centavos


@pytest.mark.parametrize("texto", ["", "abc", "1,234", "1.23", "12,345", "1.2345,00", "R$", "--1"])
def test_centavos_de_texto_invalido(texto):
    with pytest.raises(ValueError):
        centavos_de_texto(texto)


def test_ida_e_volta_texto_centavos():
    for centavos in (0, 1, 99, 100, 123456, 987654321):
        assert centavos_de_texto(formatar_brl(centavos)) == centavos


@pytest.mark.parametrize(
    ("valor", "kwargs", "esperado"),
    [
        (Decimal("4.25"), {}, "4,2%"),  # arredondamento bancário
        (Decimal("4.35"), {}, "4,4%"),
        (Decimal("-1.8"), {}, "−1,8%"),
        (Decimal("4.2"), {"sinal": True}, "+4,2%"),
        (Decimal("1234.5"), {"casas": 2}, "1.234,50%"),
        (Decimal("0"), {"sinal": True}, "0,0%"),
    ],
)
def test_formatar_percentual(valor, kwargs, esperado):
    assert formatar_percentual(valor, **kwargs) == esperado

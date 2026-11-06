from datetime import UTC, datetime

import pytest

from app.domain.competencia import FUSO, Competencia


def test_texto_ida_e_volta():
    assert str(Competencia.de_texto("2026-10")) == "2026-10"
    assert Competencia.de_texto("2026-01") == Competencia(2026, 1)


@pytest.mark.parametrize(
    "texto", ["2026-13", "2026-00", "2026-1", "26-10", "abc", "", "2026-10-01"]
)
def test_texto_invalido(texto):
    with pytest.raises(ValueError):
        Competencia.de_texto(texto)


def test_navegacao_entre_meses():
    assert Competencia(2026, 1).anterior() == Competencia(2025, 12)
    assert Competencia(2026, 12).proxima() == Competencia(2027, 1)
    assert Competencia(2026, 10).proxima().anterior() == Competencia(2026, 10)


def test_rotulos():
    assert Competencia(2026, 10).rotulo == "Outubro 2026"
    assert Competencia(2026, 3).rotulo_curto == "mar/26"


def test_ordenacao():
    assert Competencia(2025, 12) < Competencia(2026, 1)


def test_atual_usa_fuso_de_sao_paulo():
    # 1º/11 às 01:00 UTC ainda é 31/10 em São Paulo (UTC−3).
    instante = datetime(2026, 11, 1, 1, 0, tzinfo=UTC).astimezone(FUSO)
    assert Competencia.atual(instante) == Competencia(2026, 10)

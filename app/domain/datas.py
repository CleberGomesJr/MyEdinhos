"""Datas no fuso de São Paulo e aritmética de meses."""

import calendar
from datetime import date, datetime

from app.domain.competencia import FUSO


def hoje() -> date:
    return datetime.now(FUSO).date()


def somar_meses(dia: date, meses: int, dia_do_mes: int | None = None) -> date:
    """Soma meses mantendo o dia; se o mês for mais curto, usa o último dia (31/01 + 1 → 28/02)."""
    total = dia.year * 12 + dia.month - 1 + meses
    ano, mes = divmod(total, 12)
    mes += 1
    ultimo = calendar.monthrange(ano, mes)[1]
    return date(ano, mes, min(dia_do_mes or dia.day, ultimo))

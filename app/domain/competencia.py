"""Mês de competência (`YYYY-MM`), no fuso de São Paulo."""

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("America/Sao_Paulo")

MESES = (
    "Janeiro",
    "Fevereiro",
    "Março",
    "Abril",
    "Maio",
    "Junho",
    "Julho",
    "Agosto",
    "Setembro",
    "Outubro",
    "Novembro",
    "Dezembro",
)

_FORMATO = re.compile(r"(\d{4})-(\d{2})")


@dataclass(frozen=True, order=True)
class Competencia:
    ano: int
    mes: int

    def __post_init__(self) -> None:
        if not 1 <= self.mes <= 12:
            raise ValueError(f"Mês inválido: {self.mes}")
        if not 1900 <= self.ano <= 2999:
            raise ValueError(f"Ano inválido: {self.ano}")

    @classmethod
    def de_texto(cls, texto: str) -> "Competencia":
        """'2026-10' -> Competencia(2026, 10). Levanta `ValueError` se inválido."""
        encontrado = _FORMATO.fullmatch(texto.strip())
        if not encontrado:
            raise ValueError(f"Competência inválida: {texto!r} (use AAAA-MM)")
        return cls(int(encontrado[1]), int(encontrado[2]))

    @classmethod
    def de_data(cls, dia: date) -> "Competencia":
        return cls(dia.year, dia.month)

    @property
    def primeiro_dia(self) -> date:
        return date(self.ano, self.mes, 1)

    @property
    def ultimo_dia(self) -> date:
        return date(self.ano, self.mes, calendar.monthrange(self.ano, self.mes)[1])

    @classmethod
    def atual(cls, agora: datetime | None = None) -> "Competencia":
        agora = agora or datetime.now(FUSO)
        return cls(agora.year, agora.month)

    def __str__(self) -> str:
        return f"{self.ano:04d}-{self.mes:02d}"

    def anterior(self) -> "Competencia":
        return (
            Competencia(self.ano - 1, 12) if self.mes == 1 else Competencia(self.ano, self.mes - 1)
        )

    def proxima(self) -> "Competencia":
        return (
            Competencia(self.ano + 1, 1) if self.mes == 12 else Competencia(self.ano, self.mes + 1)
        )

    @property
    def rotulo(self) -> str:
        """'Outubro 2026'."""
        return f"{MESES[self.mes - 1]} {self.ano}"

    @property
    def rotulo_curto(self) -> str:
        """'out/26'."""
        return f"{MESES[self.mes - 1][:3].lower()}/{self.ano % 100:02d}"

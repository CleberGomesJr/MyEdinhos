"""Tipos de coluna para valores exatos. Dinheiro nunca passa por `float`."""

from decimal import Decimal, InvalidOperation
from enum import StrEnum

from sqlalchemy import Enum, Integer, String
from sqlalchemy.types import TypeDecorator


def EnumTexto(enum: type[StrEnum], nome: str) -> Enum:  # noqa: N802 (usado como tipo de coluna)
    """Enum guardado como TEXT com CHECK no banco (valores = `.value` do StrEnum)."""
    return Enum(
        enum,
        name=nome,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda membros: [membro.value for membro in membros],
        validate_strings=True,
    )


class Money(TypeDecorator[int]):
    """Valor monetário em centavos inteiros, guardado como INTEGER."""

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"Money espera centavos em int, recebeu {type(value).__name__}")
        return value

    def process_result_value(self, value, dialect):
        return value


class DecimalText(TypeDecorator[Decimal]):
    """`Decimal` guardado como TEXT, sem perda de precisão (quantidades, preço unitário).

    Atenção: comparações e ordenação feitas no SQL são de texto. Faça contas em Python.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, bool | float):
            raise TypeError(f"DecimalText não aceita {type(value).__name__}; use Decimal ou str")
        if isinstance(value, int | str):
            try:
                value = Decimal(value)
            except InvalidOperation as erro:
                raise ValueError(f"Valor decimal inválido: {value!r}") from erro
        if not isinstance(value, Decimal):
            raise TypeError(f"DecimalText espera Decimal, recebeu {type(value).__name__}")
        if not value.is_finite():
            raise ValueError(f"Valor decimal não finito: {value!r}")
        return format(value, "f")

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(value)

"""Leitura de formulários (texto) com mensagens de erro por campo, em pt-BR."""

from collections.abc import Mapping
from datetime import date
from enum import StrEnum

from app.domain.dinheiro import centavos_de_texto

OBRIGATORIO = "Obrigatório."


class ErroValidacao(Exception):
    """Erros por campo do formulário: {"valor": "Informe um valor maior que zero."}."""

    def __init__(self, erros: dict[str, str]):
        super().__init__("; ".join(f"{campo}: {msg}" for campo, msg in erros.items()))
        self.erros = erros


class Leitor:
    """Converte campos de texto acumulando erros; `verificar()` levanta ErroValidacao."""

    def __init__(self, dados: Mapping[str, str]):
        self.dados = dados
        self.erros: dict[str, str] = {}

    def bruto(self, campo: str) -> str:
        return str(self.dados.get(campo) or "").strip()

    def _faltando(self, campo: str, obrigatorio: bool) -> bool:
        if self.bruto(campo):
            return False
        if obrigatorio:
            self.erros[campo] = OBRIGATORIO
        return True

    def texto(self, campo: str, maximo: int, obrigatorio: bool = True) -> str | None:
        if self._faltando(campo, obrigatorio):
            return None
        valor = " ".join(self.bruto(campo).split())
        if len(valor) > maximo:
            self.erros[campo] = f"Use no máximo {maximo} caracteres."
            return None
        return valor

    def centavos(self, campo: str, positivo: bool = True, obrigatorio: bool = True) -> int | None:
        if self._faltando(campo, obrigatorio):
            return None
        try:
            valor = centavos_de_texto(self.bruto(campo))
        except ValueError:
            self.erros[campo] = "Valor inválido. Use o formato 1.234,56."
            return None
        if positivo and valor <= 0:
            self.erros[campo] = "Informe um valor maior que zero."
            return None
        return valor

    def data(self, campo: str, obrigatorio: bool = True) -> date | None:
        if self._faltando(campo, obrigatorio):
            return None
        try:
            return date.fromisoformat(self.bruto(campo))
        except ValueError:
            self.erros[campo] = "Data inválida."
            return None

    def inteiro(self, campo: str, minimo: int, maximo: int, obrigatorio: bool = True) -> int | None:
        if self._faltando(campo, obrigatorio):
            return None
        bruto = self.bruto(campo)
        if not bruto.isdigit() or not minimo <= int(bruto) <= maximo:
            self.erros[campo] = f"Use um número de {minimo} a {maximo}."
            return None
        return int(bruto)

    def id(self, campo: str, obrigatorio: bool = True) -> int | None:
        if self._faltando(campo, obrigatorio):
            return None
        bruto = self.bruto(campo)
        if not bruto.isdigit():
            self.erros[campo] = "Opção inválida."
            return None
        return int(bruto)

    def escolha[E: StrEnum](self, campo: str, enum: type[E]) -> E | None:
        if self._faltando(campo, True):
            return None
        try:
            return enum(self.bruto(campo))
        except ValueError:
            self.erros[campo] = "Opção inválida."
            return None

    def marcado(self, campo: str) -> bool:
        return self.bruto(campo).lower() in {"1", "on", "true", "sim"}

    def verificar(self) -> None:
        if self.erros:
            raise ErroValidacao(self.erros)

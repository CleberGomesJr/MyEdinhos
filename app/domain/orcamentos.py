"""Orçamento por categoria (E3-07).

O limite é das categorias **principais** de saída (subcategorias somam na mãe) e vale do mês
em que foi definido em diante, até ser mudado. Gasto = saídas do mês por competência
(pagas ou não, cartão incluído), a mesma conta da Visão geral.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import Categoria, Orcamento, TipoCategoria
from app.domain.competencia import Competencia
from app.domain.formularios import Leitor
from app.domain.resumos import saidas_por_principal

ATENCAO_A_PARTIR = 80  # % do limite


@dataclass(frozen=True)
class Linha:
    categoria: Categoria
    gasto: int
    limite: int | None  # None = sem limite

    @property
    def percentual(self) -> Decimal | None:
        if not self.limite:
            return None
        return Decimal(self.gasto * 100) / Decimal(self.limite)

    @property
    def restante(self) -> int | None:
        return None if self.limite is None else self.limite - self.gasto

    @property
    def estado(self) -> str | None:
        """ok, atencao (≥ 80%) ou critico (passou do limite)."""
        if self.percentual is None:
            return None
        if self.gasto > self.limite:
            return "critico"
        return "atencao" if self.percentual >= ATENCAO_A_PARTIR else "ok"


def limites(sessao: Session, mes: Competencia) -> dict[int, int]:
    """{categoria_id: limite} vigente no mês (o registro mais recente até ele)."""
    vigentes: dict[int, int] = {}
    registros = sessao.scalars(
        select(Orcamento).where(Orcamento.competencia <= str(mes)).order_by(Orcamento.competencia)
    )
    for registro in registros:
        vigentes[registro.categoria_id] = registro.limite_centavos
    return {categoria_id: limite for categoria_id, limite in vigentes.items() if limite}


def listar(sessao: Session, mes: Competencia) -> list[Linha]:
    """Categorias principais de saída (ativas, ou com gasto/limite no mês): primeiro as que
    têm limite, da mais usada para a menos; depois as outras, pelo gasto."""
    gastos = dict(saidas_por_principal(sessao, mes))
    vigentes = limites(sessao, mes)
    categorias = sessao.scalars(
        select(Categoria)
        .where(Categoria.tipo == TipoCategoria.SAIDA, Categoria.pai_id.is_(None))
        .order_by(Categoria.nome)
    )
    linhas = [
        Linha(categoria, gastos.get(categoria.id, 0), vigentes.get(categoria.id))
        for categoria in categorias
        if categoria.ativa or categoria.id in gastos or categoria.id in vigentes
    ]
    com_limite = sorted((ln for ln in linhas if ln.limite), key=lambda ln: -ln.percentual)
    sem_limite = sorted((ln for ln in linhas if not ln.limite), key=lambda ln: -ln.gasto)
    return com_limite + sem_limite


@dataclass(frozen=True)
class Total:
    gasto: int
    limite: int

    @property
    def percentual(self) -> Decimal | None:
        return Decimal(self.gasto * 100) / Decimal(self.limite) if self.limite else None


def total(linhas: list[Linha]) -> Total:
    """Soma só das categorias com limite (o resto não tem com o que comparar)."""
    com_limite = [linha for linha in linhas if linha.limite]
    return Total(sum(ln.gasto for ln in com_limite), sum(ln.limite for ln in com_limite))


def definir(
    sessao: Session, categoria: Categoria, mes: Competencia, dados: Mapping[str, str]
) -> int | None:
    """Define o limite de `mes` em diante (apaga o que estava marcado para meses seguintes).
    Em branco = sem limite a partir deste mês. Devolve o limite (ou None)."""
    leitor = Leitor(dados)
    limite = leitor.centavos("limite", obrigatorio=False)
    if categoria.tipo != TipoCategoria.SAIDA or categoria.pai_id is not None:
        leitor.erros["limite"] = "O orçamento é só para categorias principais de saída."
    leitor.verificar()

    sessao.execute(
        delete(Orcamento).where(
            Orcamento.categoria_id == categoria.id, Orcamento.competencia > str(mes)
        )
    )
    registro = sessao.scalar(
        select(Orcamento).where(
            Orcamento.categoria_id == categoria.id, Orcamento.competencia == str(mes)
        )
    )
    if registro is None:
        registro = Orcamento(categoria=categoria, competencia=str(mes))
        sessao.add(registro)
    registro.limite_centavos = limite or 0
    sessao.flush()
    return limite or None

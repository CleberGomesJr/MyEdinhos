"""Relatórios do ano (E3-09 e E3-10): resumo mês a mês e gasto por categoria × mês."""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Categoria
from app.domain import resumos
from app.domain.competencia import Competencia
from app.domain.resumos import Resultado


def meses_do_ano(ano: int) -> list[Competencia]:
    return [Competencia(ano, mes) for mes in range(1, 13)]


@dataclass(frozen=True)
class ResumoAnual:
    meses: list[tuple[Competencia, Resultado]]
    ate: Competencia | None = None  # médias e melhor/pior só até aqui (meses futuros são previsão)

    @property
    def total(self) -> Resultado:
        return Resultado(
            sum(r.entradas for _, r in self.meses), sum(r.saidas for _, r in self.meses)
        )

    @property
    def com_movimento(self) -> list[tuple[Competencia, Resultado]]:
        return [
            (mes, r)
            for mes, r in self.meses
            if (r.entradas or r.saidas) and (self.ate is None or mes <= self.ate)
        ]

    @property
    def media_saidas(self) -> int:
        """Média das saídas nos meses com movimento (meses vazios não puxam a média para baixo)."""
        meses = self.com_movimento
        return sum(r.saidas for _, r in meses) // len(meses) if meses else 0

    @property
    def media_resultado(self) -> int:
        meses = self.com_movimento
        return sum(r.saldo for _, r in meses) // len(meses) if meses else 0

    @property
    def melhor(self) -> tuple[Competencia, Resultado] | None:
        return max(self.com_movimento, key=lambda item: item[1].saldo, default=None)

    @property
    def pior(self) -> tuple[Competencia, Resultado] | None:
        return min(self.com_movimento, key=lambda item: item[1].saldo, default=None)


def resumo_anual(
    sessao: Session, ano: int, caixa: bool = False, ate: Competencia | None = None
) -> ResumoAnual:
    return ResumoAnual(resumos.fluxo(sessao, Competencia(ano, 12), 12, caixa=caixa), ate)


@dataclass(frozen=True)
class LinhaMapa:
    categoria: Categoria
    valores: list[int]  # um por mês (janeiro a dezembro)

    @property
    def total(self) -> int:
        return sum(self.valores)


@dataclass(frozen=True)
class MapaCategorias:
    linhas: list[LinhaMapa]
    maior: int  # maior célula, para a intensidade da cor

    def intensidade(self, valor: int) -> int:
        """0–100: quanto do tom de destaque a célula recebe (rampa de um tom só)."""
        if not valor or not self.maior:
            return 0
        return max(8, round(Decimal(valor * 100) / Decimal(self.maior)))

    @property
    def totais_por_mes(self) -> list[int]:
        return [sum(linha.valores[i] for linha in self.linhas) for i in range(12)]


def mapa_categorias(sessao: Session, ano: int) -> MapaCategorias:
    """Saídas (competência) por categoria principal em cada mês do ano, da maior para a menor."""
    por_categoria: dict[int, list[int]] = {}
    for indice, mes in enumerate(meses_do_ano(ano)):
        for categoria_id, valor in resumos.saidas_por_principal(sessao, mes):
            por_categoria.setdefault(categoria_id, [0] * 12)[indice] = valor
    categorias = {
        c.id: c
        for c in sessao.scalars(select(Categoria).where(Categoria.id.in_(list(por_categoria))))
    }
    linhas = sorted(
        (LinhaMapa(categorias[cid], valores) for cid, valores in por_categoria.items()),
        key=lambda linha: -linha.total,
    )
    maior = max((v for linha in linhas for v in linha.valores), default=0)
    return MapaCategorias(linhas, maior)

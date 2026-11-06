"""Indicadores de dividendos por ativo ⭐ (E6-11).

Base: proventos divulgados pela fonte (`proventos_anunciados`, valor por cota/ação pela data
com). Payout fica de fora: nenhuma fonte gratuita conferida entrega lucro por ação de forma
confiável (vault "05 - Fontes de Dados").
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Ativo, ProventoAnunciado

CORTE = Decimal("0.75")  # pagar menos de 75% do ano anterior conta como corte


@dataclass
class Indicadores:
    ativo: Ativo
    por_ano: dict[int, Decimal] = field(default_factory=dict)  # valor por unidade
    pagamentos_12m: int = 0
    por_unidade_12m: Decimal = Decimal(0)
    por_unidade_12m_anterior: Decimal = Decimal(0)
    ultimo_pagamento: date | None = None

    @property
    def tem_historico(self) -> bool:
        return bool(self.por_ano)

    @property
    def frequencia(self) -> str:
        n = self.pagamentos_12m
        if n >= 10:
            return "mensal"
        if n >= 3:
            return "trimestral" if n <= 5 else "frequente"
        if n == 2:
            return "semestral"
        return "anual" if n == 1 else "sem pagamentos em 12 meses"

    def _anos_fechados(self, ano_atual: int) -> list[int]:
        if not self.por_ano:
            return []
        return list(range(min(self.por_ano), ano_atual))

    def anos_seguidos(self, ano_atual: int) -> int:
        """Anos fechados seguidos (até o ano passado) com algum provento."""
        seguidos = 0
        for ano in reversed(self._anos_fechados(ano_atual)):
            if self.por_ano.get(ano, Decimal(0)) <= 0:
                break
            seguidos += 1
        return seguidos

    def cortes(self, ano_atual: int) -> list[int]:
        """Anos fechados que pagaram menos de 75% do ano anterior (ou nada)."""
        anos = self._anos_fechados(ano_atual)
        cortes = []
        for anterior, ano in zip(anos, anos[1:], strict=False):
            pago_antes = self.por_ano.get(anterior, Decimal(0))
            if pago_antes > 0 and self.por_ano.get(ano, Decimal(0)) < pago_antes * CORTE:
                cortes.append(ano)
        return cortes

    def crescimento_anual(self, ano_atual: int) -> Decimal | None:
        """Crescimento médio ao ano do provento por unidade (anos fechados, mínimo 3)."""
        anos = [a for a in self._anos_fechados(ano_atual) if self.por_ano.get(a, 0) > 0]
        if len(anos) < 3:
            return None
        inicio, fim = self.por_ano[anos[0]], self.por_ano[anos[-1]]
        periodos = anos[-1] - anos[0]
        return ((fim / inicio) ** (Decimal(1) / periodos) - 1) * 100

    @property
    def variacao_12m(self) -> Decimal | None:
        """Proventos por unidade dos últimos 12 meses × 12 meses anteriores, em %."""
        if not self.por_unidade_12m_anterior:
            return None
        return (self.por_unidade_12m / self.por_unidade_12m_anterior - 1) * 100


def indicadores(sessao: Session, ativos: list[Ativo], hoje: date) -> dict[int, Indicadores]:
    resultado = {ativo.id: Indicadores(ativo) for ativo in ativos}
    if not resultado:
        return resultado
    um_ano, dois_anos = hoje - timedelta(days=365), hoje - timedelta(days=730)
    anuncios = sessao.scalars(
        select(ProventoAnunciado).where(
            ProventoAnunciado.ativo_id.in_(list(resultado)), ProventoAnunciado.data_com <= hoje
        )
    )
    for anuncio in anuncios:
        item = resultado[anuncio.ativo_id]
        valor = anuncio.valor_por_unidade
        item.por_ano[anuncio.data_com.year] = (
            item.por_ano.get(anuncio.data_com.year, Decimal(0)) + valor
        )
        if anuncio.data_com > um_ano:
            item.pagamentos_12m += 1
            item.por_unidade_12m += valor
        elif anuncio.data_com > dois_anos:
            item.por_unidade_12m_anterior += valor
        if item.ultimo_pagamento is None or anuncio.data_com > item.ultimo_pagamento:
            item.ultimo_pagamento = anuncio.data_com
    return resultado

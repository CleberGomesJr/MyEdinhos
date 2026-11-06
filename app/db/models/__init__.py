"""Modelos do banco. Importe cada módulo aqui para o Alembic enxergá-lo no autogenerate."""

from app.db.base import Base
from app.db.models.cartoes import Cartao, CompraNoFechamento, Fatura
from app.db.models.categorias import Categoria, TipoCategoria
from app.db.models.contas import Conta, TipoConta
from app.db.models.desfazer import Desfazer
from app.db.models.investimentos import (
    Ativo,
    ClasseAtivo,
    Cotacao,
    Indexador,
    IndiceMacro,
    Operacao,
    ProventoAnunciado,
    TipoOperacao,
    TipoProvento,
    TituloTesouro,
)
from app.db.models.lancamentos import Frequencia, Lancamento, Recorrencia, TipoLancamento
from app.db.models.objetivos import GrupoAlocacao, MetaAlocacao, Objetivo
from app.db.models.orcamentos import Orcamento
from app.db.models.preferencias import Preferencia

__all__ = [
    "Ativo",
    "ClasseAtivo",
    "Cotacao",
    "IndiceMacro",
    "Indexador",
    "Operacao",
    "ProventoAnunciado",
    "TipoOperacao",
    "TipoProvento",
    "TituloTesouro",
    "Base",
    "Cartao",
    "CompraNoFechamento",
    "Fatura",
    "Categoria",
    "Conta",
    "Desfazer",
    "Frequencia",
    "Lancamento",
    "GrupoAlocacao",
    "MetaAlocacao",
    "Objetivo",
    "Orcamento",
    "Preferencia",
    "Recorrencia",
    "TipoCategoria",
    "TipoConta",
    "TipoLancamento",
]

"""Exclusão com "Desfazer": guarda uma cópia JSON das linhas e as reinsere com o mesmo id."""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum

from sqlalchemy import Date, DateTime, delete, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base, agora_utc
from app.db.models import Desfazer

VALIDADE = timedelta(days=7)


def _para_json(valor):
    if isinstance(valor, datetime | date):
        return valor.isoformat()
    if isinstance(valor, Enum):
        return valor.value
    if isinstance(valor, Decimal):
        return format(valor, "f")  # DecimalText aceita o texto de volta
    return valor


def excluir_com_desfazer(sessao: Session, linhas: list[Base], descricao: str) -> Desfazer:
    """Apaga as linhas (todas da mesma tabela) e devolve o registro para desfazer."""
    tabela = linhas[0].__table__
    copia = [
        {col.name: _para_json(getattr(linha, col.key)) for col in tabela.columns}
        for linha in linhas
    ]
    sessao.execute(delete(Desfazer).where(Desfazer.criado_em < agora_utc() - VALIDADE))
    registro = Desfazer(tabela=tabela.name, descricao=descricao, dados=json.dumps(copia))
    sessao.add(registro)
    for linha in linhas:
        sessao.delete(linha)
    sessao.flush()
    return registro


def desfazer(sessao: Session, registro_id: int) -> str | None:
    """Reinsere as linhas. Devolve a descrição, ou None se não houver o que desfazer."""
    registro = sessao.get(Desfazer, registro_id)
    if registro is None:
        return None
    tabela = Base.metadata.tables[registro.tabela]
    linhas = json.loads(registro.dados)
    for linha in linhas:
        for coluna in tabela.columns:
            valor = linha.get(coluna.name)
            if valor is None:
                continue
            if isinstance(coluna.type, DateTime):
                linha[coluna.name] = datetime.fromisoformat(valor)
            elif isinstance(coluna.type, Date):
                linha[coluna.name] = date.fromisoformat(valor)
    try:
        with sessao.begin_nested():
            sessao.execute(insert(tabela), linhas)
    except IntegrityError:
        return None
    sessao.delete(registro)
    sessao.flush()
    return registro.descricao

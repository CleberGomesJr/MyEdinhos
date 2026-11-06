"""Preferências e estado do app (chave/valor)."""

from sqlalchemy.orm import Session

from app.db.models import Preferencia


def obter(sessao: Session, chave: str) -> str | None:
    preferencia = sessao.get(Preferencia, chave)
    return preferencia.valor if preferencia else None


def definir(sessao: Session, chave: str, valor: str) -> None:
    preferencia = sessao.get(Preferencia, chave)
    if preferencia is None:
        sessao.add(Preferencia(chave=chave, valor=valor))
    else:
        preferencia.valor = valor
    sessao.flush()

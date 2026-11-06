from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, agora_utc


class Preferencia(Base):
    """Estado do app em chave/valor (ex.: se o assistente de cadastro inicial foi concluído)."""

    __tablename__ = "preferencias"

    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(Text)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime, default=agora_utc, onupdate=agora_utc)

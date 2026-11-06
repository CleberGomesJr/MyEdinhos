from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, agora_utc


class Desfazer(Base):
    """Cópia (JSON) de linhas excluídas, para o botão "Desfazer" do toast poder reinseri-las."""

    __tablename__ = "desfazer"

    id: Mapped[int] = mapped_column(primary_key=True)
    tabela: Mapped[str] = mapped_column(String(40))
    descricao: Mapped[str] = mapped_column(String(200))
    dados: Mapped[str] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora_utc)

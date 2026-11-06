from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CamposPadrao
from app.db.types import EnumTexto


class TipoCategoria(StrEnum):
    ENTRADA = "entrada"
    SAIDA = "saida"

    @property
    def rotulo(self) -> str:
        return {"entrada": "Entrada", "saida": "Saída"}[self.value]


class Categoria(CamposPadrao, Base):
    __tablename__ = "categorias"
    # cor_slot 1–8 = paleta categórica (--s1…--s8); 0 = cinza de "Outros" (--s-outros).
    __table_args__ = (CheckConstraint("cor_slot BETWEEN 0 AND 8", name="cor_slot"),)

    nome: Mapped[str] = mapped_column(String(40))
    tipo: Mapped[TipoCategoria] = mapped_column(EnumTexto(TipoCategoria, "tipo_categoria"))
    pai_id: Mapped[int | None] = mapped_column(ForeignKey("categorias.id", ondelete="RESTRICT"))
    cor_slot: Mapped[int]
    icone: Mapped[str] = mapped_column(String(40), default="circle-ellipsis")
    ativa: Mapped[bool] = mapped_column(default=True)

    pai: Mapped["Categoria | None"] = relationship(
        remote_side="Categoria.id", back_populates="subcategorias"
    )
    subcategorias: Mapped[list["Categoria"]] = relationship(
        back_populates="pai", order_by="Categoria.nome"
    )

    @property
    def nome_completo(self) -> str:
        return f"{self.pai.nome} › {self.nome}" if self.pai else self.nome

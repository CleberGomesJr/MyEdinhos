from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CamposPadrao
from app.db.models.categorias import Categoria
from app.db.types import Money


class Orcamento(CamposPadrao, Base):
    """Limite de gasto de uma categoria principal, valendo a partir de `competencia`.

    Vale para os meses seguintes até outro registro da mesma categoria; limite 0 = sem limite.
    """

    __tablename__ = "orcamentos"
    __table_args__ = (
        UniqueConstraint("categoria_id", "competencia", name="uq_orcamentos_categoria_competencia"),
        CheckConstraint("limite_centavos >= 0", name="limite_nao_negativo"),
    )

    categoria_id: Mapped[int] = mapped_column(ForeignKey("categorias.id", ondelete="CASCADE"))
    competencia: Mapped[str] = mapped_column(String(7))  # AAAA-MM
    limite_centavos: Mapped[int] = mapped_column(Money)

    categoria: Mapped[Categoria] = relationship()

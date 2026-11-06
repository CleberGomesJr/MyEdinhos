from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.db.session import criar_engine
from app.db.types import DecimalText, Money


class _BaseTeste(DeclarativeBase):
    pass


class Valor(_BaseTeste):
    __tablename__ = "valores"
    id: Mapped[int] = mapped_column(primary_key=True)
    centavos: Mapped[int | None] = mapped_column(Money)
    quantidade: Mapped[Decimal | None] = mapped_column(DecimalText)


@pytest.fixture
def sessao(tmp_path):
    engine = criar_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    _BaseTeste.metadata.create_all(engine)
    with Session(engine) as sessao:
        yield sessao
    engine.dispose()


def _ida_e_volta(sessao: Session, **campos) -> Valor:
    sessao.add(Valor(id=1, **campos))
    sessao.commit()
    sessao.expunge_all()
    return sessao.scalars(select(Valor)).one()


def test_decimal_sem_perda_de_precisao(sessao):
    lido = _ida_e_volta(sessao, quantidade=Decimal("0.1") + Decimal("0.2"))
    assert lido.quantidade == Decimal("0.3")
    assert isinstance(lido.quantidade, Decimal)


def test_decimal_preserva_casas_e_numeros_grandes(sessao):
    lido = _ida_e_volta(sessao, quantidade=Decimal("123456789012345678.12345678"))
    assert lido.quantidade == Decimal("123456789012345678.12345678")


def test_decimal_aceita_texto(sessao):
    assert _ida_e_volta(sessao, quantidade="12.50").quantidade == Decimal("12.50")


def test_money_em_centavos(sessao):
    lido = _ida_e_volta(sessao, centavos=10 + 20)
    assert lido.centavos == 30
    assert isinstance(lido.centavos, int)


def test_nulos(sessao):
    lido = _ida_e_volta(sessao)
    assert lido.centavos is None
    assert lido.quantidade is None


@pytest.mark.parametrize("campos", [{"centavos": 0.3}, {"centavos": True}, {"quantidade": 0.3}])
def test_float_e_recusado(sessao, campos):
    sessao.add(Valor(id=1, **campos))
    with pytest.raises(StatementError, match="TypeError"):
        sessao.commit()


@pytest.mark.parametrize("texto", ["abc", "NaN", "Infinity"])
def test_decimal_invalido_e_recusado(sessao, texto):
    sessao.add(Valor(id=1, quantidade=texto))
    with pytest.raises(StatementError, match="ValueError"):
        sessao.commit()

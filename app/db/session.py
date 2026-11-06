"""Engine e sessões do SQLite."""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def criar_engine(url: str) -> Engine:
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(conexao_dbapi, _registro):
        cursor = conexao_dbapi.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


def criar_fabrica_sessao(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def garantir_transacao(sessao: Session) -> None:
    """O sqlite3 só abre transação antes de escrever. Sem ela, o primeiro SAVEPOINT faz esse papel
    e o RELEASE grava de vez: nem o rollback da sessão desfaz. Chamar antes de `begin_nested`
    quando o que vem depois pode ter de ser desfeito."""
    conexao = sessao.connection().connection.dbapi_connection
    if not conexao.in_transaction:
        conexao.execute("BEGIN")


def obter_sessao(request: Request) -> Iterator[Session]:
    """Dependência do FastAPI: uma sessão por requisição."""
    with request.app.state.fabrica_sessao() as sessao:
        yield sessao

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import get_settings
from app.db.models import Base

config = context.config

# Chamado por app.db.migracoes, o app já configurou os próprios logs.
if config.config_file_name and not config.attributes.get("sem_log"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    return config.attributes.get("url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Engine próprio, SEM `PRAGMA foreign_keys=ON`: o modo batch recria tabelas, e um
    # DROP TABLE com chaves estrangeiras ligadas dispararia ON DELETE CASCADE (perda de dados).
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as conexao:
        context.configure(
            connection=conexao,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

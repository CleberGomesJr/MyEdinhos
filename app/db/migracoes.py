"""Aplicação das migrações Alembic a partir do código."""

from alembic import command
from alembic.config import Config

from app.config import RAIZ


def config_alembic(url: str) -> Config:
    config = Config(str(RAIZ / "alembic.ini"))
    config.attributes["url"] = url
    config.attributes["sem_log"] = True
    return config


def aplicar_migracoes(url: str) -> None:
    command.upgrade(config_alembic(url), "head")

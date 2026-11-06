"""Configuração do app, lida do `.env` na raiz do projeto (prefixo `MYEDINHOS_`)."""

import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parent
RAIZ = APP_DIR.parent

# O servidor só escuta na máquina local; não é configurável de propósito.
HOST = "127.0.0.1"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=RAIZ / ".env",
        env_file_encoding="utf-8",
        env_prefix="MYEDINHOS_",
        extra="ignore",
    )

    data_dir: Path = RAIZ / "data"
    porta: int = 8000
    nivel_log: str = "INFO"
    modo_demo: bool = False  # dados fictícios (E7-01): mostra o aviso e não busca mercado
    # Token gratuito da brapi.dev (E5-02). Sem ele, as cotações vêm do Yahoo.
    brapi_token: str | None = Field(
        default=None, validation_alias=AliasChoices("MYEDINHOS_BRAPI_TOKEN", "BRAPI_TOKEN")
    )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "financas.db"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"

    def garantir_pastas(self) -> None:
        for pasta in (self.data_dir, self.backups_dir, self.logs_dir):
            pasta.mkdir(parents=True, exist_ok=True)
            if os.name == "posix":
                pasta.chmod(0o700)  # só o dono; no Windows valem as permissões da pasta


@lru_cache
def get_settings() -> Settings:
    return Settings()

"""Backup do banco com a API de backup do SQLite (seguro com o banco em uso / WAL)."""

import logging
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

MANTER = 14
# Só arquivos com este nome são apagados na limpeza; cópias manuais ficam intactas.
PADRAO_NOME = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}\.db$")


def fazer_backup(
    caminho_db: Path, pasta: Path, manter: int = MANTER, agora: datetime | None = None
) -> Path | None:
    """Copia o banco para `pasta/AAAA-MM-DD_HHMM.db` e mantém só os `manter` mais recentes."""
    if not caminho_db.exists():
        return None
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / f"{agora or datetime.now():%Y-%m-%d_%H%M}.db"
    with closing(sqlite3.connect(caminho_db)) as origem, closing(sqlite3.connect(destino)) as copia:
        origem.backup(copia)
    destino.chmod(0o600)  # só o dono (no Windows, só afeta "somente leitura")
    log.info("Backup criado em %s", destino)
    _limpar_antigos(pasta, manter)
    return destino


def _limpar_antigos(pasta: Path, manter: int) -> None:
    backups = sorted(p for p in pasta.iterdir() if PADRAO_NOME.match(p.name))
    for antigo in backups[:-manter]:
        antigo.unlink()
        log.info("Backup antigo removido: %s", antigo.name)

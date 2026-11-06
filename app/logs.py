"""Log em arquivo rotativo em `data/logs/app.log`."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMATO = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"


class _HandlerDoApp(RotatingFileHandler):
    """Marca o handler para que reconfigurar não o duplique."""


def configurar_logs(pasta: Path, nivel: str = "INFO") -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    raiz = logging.getLogger()
    for handler in [h for h in raiz.handlers if isinstance(h, _HandlerDoApp)]:
        raiz.removeHandler(handler)
        handler.close()

    handler = _HandlerDoApp(
        pasta / "app.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8", delay=True
    )
    handler.setFormatter(logging.Formatter(FORMATO))
    raiz.addHandler(handler)
    raiz.setLevel(nivel.upper())

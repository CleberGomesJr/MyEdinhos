"""Testes de ponta a ponta (E7-03): servidor real + navegador real (Edge instalado).

Rodam à parte: `uv run e2e`. Não entram no `uv run check`, que precisa ser rápido.
Cada servidor usa uma pasta temporária: nunca o banco real.
"""

import logging
import threading
import time
import urllib.request
from datetime import date
from pathlib import Path

import pytest
import uvicorn

from app import demo
from app.config import Settings
from app.db.migracoes import aplicar_migracoes
from app.db.session import criar_engine, criar_fabrica_sessao
from app.domain import contas
from app.logs import _HandlerDoApp
from app.main import criar_app

playwright_api = pytest.importorskip("playwright.sync_api")

PORTA_VAZIO = 8021
PORTA_DEMO = 8023


def _subir(settings: Settings, porta: int):
    config = uvicorn.Config(criar_app(settings), host="127.0.0.1", port=porta, log_level="warning")
    servidor = uvicorn.Server(config)
    thread = threading.Thread(target=servidor.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{porta}"
    for _ in range(100):
        try:
            urllib.request.urlopen(f"{url}/saude", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    return url, servidor, thread


@pytest.fixture(scope="session")
def navegador():
    with playwright_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="msedge")
        except playwright_api.Error as erro:
            pytest.skip(f"Edge não disponível: {erro}")
        yield browser
        browser.close()


@pytest.fixture
def url_vazio(tmp_path: Path):
    """App com banco novo (só as categorias padrão) e uma conta, recriado a cada teste."""
    settings = Settings(data_dir=tmp_path / "data", brapi_token=None)
    settings.garantir_pastas()
    aplicar_migracoes(settings.database_url)
    engine = criar_engine(settings.database_url)
    with criar_fabrica_sessao(engine)() as sessao:
        contas.salvar(
            sessao,
            {"nome": "Conta Teste", "tipo": "corrente", "saldo_inicial": "0",
             "data_saldo_inicial": date.today().isoformat()},
        )  # fmt: skip
        sessao.commit()
    engine.dispose()
    url, servidor, thread = _subir(settings, PORTA_VAZIO)
    yield url
    servidor.should_exit = True
    thread.join(timeout=5)
    _fechar_logs()


@pytest.fixture(scope="module")
def url_demo(tmp_path_factory):
    """App com os 12 meses fictícios (tela cheia de dados e gráficos)."""
    base = Settings(data_dir=tmp_path_factory.mktemp("demo") / "data", brapi_token=None)
    base.garantir_pastas()
    url, servidor, thread = _subir(demo.gerar(base), PORTA_DEMO)
    yield url
    servidor.should_exit = True
    thread.join(timeout=5)
    _fechar_logs()


def _fechar_logs() -> None:
    raiz = logging.getLogger()
    for handler in [h for h in raiz.handlers if isinstance(h, _HandlerDoApp)]:
        raiz.removeHandler(handler)
        handler.close()


@pytest.fixture
def aba(navegador):
    """Aba que junta erros de JavaScript, de CSP e de recursos que não carregaram."""
    contexto = navegador.new_context(
        viewport={"width": 1440, "height": 900}, reduced_motion="reduce", locale="pt-BR"
    )
    pagina = contexto.new_page()
    pagina.erros = []
    pagina.on("pageerror", lambda erro: pagina.erros.append(str(erro)))
    pagina.on(
        "console",
        lambda m: pagina.erros.append(m.text) if m.type == "error" else None,
    )
    pagina.on(
        "response",
        lambda r: pagina.erros.append(f"{r.status} {r.url}") if r.status >= 400 else None,
    )
    yield pagina
    contexto.close()

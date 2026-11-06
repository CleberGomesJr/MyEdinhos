import logging
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db.migracoes import aplicar_migracoes
from app.domain import contas
from app.integrations.mercado import tesouro
from app.logs import _HandlerDoApp
from app.main import criar_app


@pytest.fixture(autouse=True)
def sem_download_do_tesouro(monkeypatch):
    """O arquivo do Tesouro é grande e fica fora da internet nos testes."""
    monkeypatch.setattr(tesouro, "baixar", lambda: iter(()))


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def app(settings):
    settings.garantir_pastas()
    aplicar_migracoes(settings.database_url)
    app = criar_app(settings)
    # O banco novo vem vazio (só categorias padrão); os testes criam a própria conta.
    with app.state.fabrica_sessao() as sessao:
        contas.salvar(
            sessao,
            {"nome": "Conta Teste", "tipo": "corrente", "saldo_inicial": "0",
             "data_saldo_inicial": date.today().isoformat()},
        )  # fmt: skip
        sessao.commit()
    yield app
    # Libera o arquivo de log do diretório temporário (Windows não apaga arquivo aberto).
    raiz = logging.getLogger()
    for handler in [h for h in raiz.handlers if isinstance(h, _HandlerDoApp)]:
        raiz.removeHandler(handler)
        handler.close()


@pytest.fixture
def sessao(app):
    """Sessão no banco migrado (categorias padrão + a "Conta Teste" dos testes)."""
    with app.state.fabrica_sessao() as sessao:
        yield sessao


@pytest.fixture
def cliente(app):
    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as cliente:
        yield cliente

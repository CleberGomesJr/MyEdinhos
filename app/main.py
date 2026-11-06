"""Cria o app FastAPI. Servido via `uvicorn app.main:criar_app --factory` (ver app/cli.py)."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import APP_DIR, Settings, get_settings
from app.db.session import criar_engine, criar_fabrica_sessao
from app.erros import registrar_erros
from app.logs import configurar_logs
from app.routers import (
    analise,
    assistente,
    carteira,
    cartoes,
    categorias,
    configuracoes,
    contas,
    desfazer,
    design,
    importacao,
    lancamentos,
    mercado,
    orcamento,
    proventos,
    relatorios,
    saude,
    visao_geral,
)
from app.seguranca import proteger
from app.web import lembrar_mes


def criar_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.garantir_pastas()
    configurar_logs(settings.logs_dir, settings.nivel_log)
    engine = criar_engine(settings.database_url)

    @asynccontextmanager
    async def ciclo_de_vida(_app: FastAPI):
        yield
        engine.dispose()

    app = FastAPI(
        title="MyEdinhos",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=ciclo_de_vida,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.fabrica_sessao = criar_fabrica_sessao(engine)

    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    app.middleware("http")(lembrar_mes)
    app.middleware("http")(proteger)  # o último registrado roda primeiro
    registrar_erros(app)
    app.include_router(saude.router)
    app.include_router(visao_geral.router)
    app.include_router(design.router)
    app.include_router(importacao.router)  # antes: /lancamentos/{id} também casaria com "importar"
    app.include_router(lancamentos.router)
    app.include_router(contas.router)
    app.include_router(cartoes.router)
    app.include_router(assistente.router)
    app.include_router(orcamento.router)
    app.include_router(relatorios.router)
    app.include_router(carteira.router)
    app.include_router(proventos.router)
    app.include_router(mercado.router)
    app.include_router(analise.router)
    app.include_router(categorias.router)
    app.include_router(desfazer.router)
    app.include_router(configuracoes.router)
    return app

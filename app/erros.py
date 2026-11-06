"""Páginas de erro amigáveis em pt-BR."""

import logging

from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.web import templates

log = logging.getLogger(__name__)

MENSAGENS = {
    404: ("Página não encontrada", "O endereço que você abriu não existe ou foi movido."),
    405: ("Ação não permitida", "Esta página não aceita esse tipo de ação."),
    500: ("Algo deu errado", "Ocorreu um erro inesperado. Ele foi registrado no log do app."),
}


def _pagina_erro(request: Request, status: int, headers: dict[str, str] | None = None):
    titulo, mensagem = MENSAGENS.get(status, ("Algo deu errado", "Não foi possível concluir."))
    return templates.TemplateResponse(
        request,
        "pages/erro.html",
        {"status": status, "titulo": titulo, "mensagem": mensagem},
        status_code=status,
        headers=headers,
    )


def registrar_erros(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def erro_http(request: Request, exc: StarletteHTTPException):
        return _pagina_erro(request, exc.status_code, exc.headers)

    @app.exception_handler(Exception)
    async def erro_interno(request: Request, exc: Exception):
        log.exception("Erro não tratado em %s %s", request.method, request.url.path)
        return _pagina_erro(request, 500)

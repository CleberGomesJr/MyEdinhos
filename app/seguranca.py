"""Proteções do servidor local (E7-08). O app não tem login: quem chega em 127.0.0.1 entra.

Por isso outro site aberto no navegador não pode falar com ele:
- `Host` fora da lista → 400 (DNS rebinding: um domínio que aponta para 127.0.0.1);
- envio (POST, PUT, PATCH, DELETE) vindo de outra origem → 403 (CSRF);
- cabeçalhos que impedem embutir a página e carregar código de fora.
"""

from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import PlainTextResponse

HOSTS_PERMITIDOS = {"127.0.0.1", "localhost"}
METODOS_SEGUROS = {"GET", "HEAD", "OPTIONS"}

CABECALHOS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # Não `no-referrer`: com ele, formulário comum (sem HTMX) sai com `Origin: null` e cai no CSRF.
    "Referrer-Policy": "same-origin",
}


def _nome_do_host(host: str) -> str:
    return urlsplit(f"//{host}").hostname or ""


def _envio_de_fora(request: Request) -> bool:
    origem = request.headers.get("origin")
    if origem is not None:
        return urlsplit(origem).netloc != request.headers.get("host", "")
    return request.headers.get("sec-fetch-site") in {"cross-site", "same-site"}


async def proteger(request: Request, call_next):
    if _nome_do_host(request.headers.get("host", "")) not in HOSTS_PERMITIDOS:
        return PlainTextResponse("Endereço não permitido.", status_code=400)
    if request.method not in METODOS_SEGUROS and _envio_de_fora(request):
        return PlainTextResponse("Envio de outro site bloqueado.", status_code=403)
    resposta = await call_next(request)
    resposta.headers.update(CABECALHOS)
    return resposta

"""Templates Jinja2 compartilhados pelos routers, com contexto, filtros e ícones globais."""

import json
import re
from datetime import UTC, date, datetime
from functools import lru_cache

from fastapi import HTTPException, Request, Response
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy.orm import Session

from app.config import APP_DIR
from app.domain.competencia import FUSO, Competencia
from app.domain.dinheiro import formatar_brl, formatar_percentual, formatar_preco
from app.domain.operacoes import formatar_quantidade
from app.navegacao import GRUPOS, SO_NA_BUSCA, item_ativo

COOKIE_MES = "mes"
COOKIE_SIDEBAR = "sidebar"
PASTA_ICONES = APP_DIR / "static" / "icons"
_NOME_ICONE = re.compile(r"[a-z0-9-]+")


def mes_da_requisicao(request: Request) -> Competencia:
    """Mês selecionado: `?mes=AAAA-MM`, senão o cookie, senão o mês atual."""
    for fonte in (request.query_params.get("mes"), request.cookies.get(COOKIE_MES)):
        if fonte:
            try:
                return Competencia.de_texto(fonte)
            except ValueError:
                pass
    return Competencia.atual()


COOKIE_VISAO = "visao"


def visao_caixa(request: Request) -> bool:
    """Resumos por caixa (E3-14)? `?visao=caixa|competencia`, senão o cookie; padrão competência."""
    visao = request.query_params.get("visao") or request.cookies.get(COOKIE_VISAO)
    return visao == "caixa"


async def lembrar_mes(request: Request, call_next):
    """Middleware: guarda no cookie (de sessão) o mês escolhido por `?mes=`."""
    resposta = await call_next(request)
    if mes := request.query_params.get("mes"):
        try:
            Competencia.de_texto(mes)
        except ValueError:
            return resposta
        resposta.set_cookie(COOKIE_MES, mes, httponly=True, samesite="lax")
    if (visao := request.query_params.get("visao")) in ("caixa", "competencia"):
        resposta.set_cookie(COOKIE_VISAO, visao, httponly=True, samesite="lax")
    return resposta


def e_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def eventos_htmx(
    mensagem: str | None = None,
    *,
    tipo: str = "ok",
    desfazer: str | None = None,
    fechar_drawer: bool = False,
    atualizar: bool = False,
) -> dict[str, str]:
    """Cabeçalho `HX-Trigger`: toast (com "Desfazer" opcional, via POST), fechar o drawer e
    `atualizar` (as listas da página escutam esse evento e se recarregam)."""
    eventos: dict = {}
    if mensagem:
        eventos["toast"] = {"mensagem": mensagem, "tipo": tipo}
        if desfazer:
            eventos["toast"]["desfazer"] = desfazer
    if fechar_drawer:
        eventos["fechar-drawer"] = True
    if atualizar:
        eventos["atualizar"] = True
    return {"HX-Trigger": json.dumps(eventos)}


def resposta_acao(
    mensagem: str, *, tipo: str = "ok", desfazer: str | None = None, fechar_drawer: bool = True
) -> Response:
    """204 para ações concluídas: mostra o toast, fecha o drawer e atualiza as listas."""
    return Response(
        status_code=204,
        headers=eventos_htmx(
            mensagem, tipo=tipo, desfazer=desfazer, fechar_drawer=fechar_drawer, atualizar=True
        ),
    )


def obter_ou_404[M](sessao: Session, modelo: type[M], ident: int) -> M:
    objeto = sessao.get(modelo, ident)
    if objeto is None:
        raise HTTPException(status_code=404)
    return objeto


@lru_cache
def _svg_icone(nome: str) -> str:
    if not _NOME_ICONE.fullmatch(nome):
        raise ValueError(f"Nome de ícone inválido: {nome!r}")
    svg = (PASTA_ICONES / f"{nome}.svg").read_text(encoding="utf-8")
    svg = re.sub(r"<!--.*?-->", "", svg, flags=re.DOTALL)
    # Só na tag <svg>: os <rect> internos também têm width/height e precisam deles.
    abertura = re.search(r"<svg\b[^>]*>", svg)
    limpa = re.sub(r'\s(?:class|width|height)="[^"]*"', "", abertura[0])
    svg = svg[: abertura.start()] + limpa + svg[abertura.end() :]
    return " ".join(svg.split())


def icone(nome: str, classe: str = "") -> Markup:
    """SVG Lucide inline, decorativo (o texto ao lado ou o aria-label dá o sentido)."""
    atributos = f'class="{f"icone {classe}".strip()}" aria-hidden="true" focusable="false"'
    return Markup(_svg_icone(nome).replace("<svg", f"<svg {atributos}", 1))


def estatico(caminho: str) -> str:
    """URL de um arquivo em static/ com `?v=` da data de modificação: o navegador não usa
    CSS/JS antigos do cache depois de uma atualização."""
    arquivo = APP_DIR / "static" / caminho
    versao = int(arquivo.stat().st_mtime) if arquivo.exists() else 0
    return f"/static/{caminho}?v={versao}"


def _data_br(valor: date) -> str:
    return f"{valor:%d/%m/%Y}"


def _hora_local(valor: datetime) -> str:
    """Data/hora gravada em UTC → "31/10 às 18:02" no fuso de São Paulo."""
    return valor.replace(tzinfo=UTC).astimezone(FUSO).strftime("%d/%m às %H:%M")


def _contexto(request: Request) -> dict:
    return {
        "mes": mes_da_requisicao(request),
        "navegacao": GRUPOS,
        "so_na_busca": SO_NA_BUSCA,
        "pagina_atual": item_ativo(request.url.path),
        "sidebar_recolhida": request.cookies.get(COOKIE_SIDEBAR) == "recolhida",
        "caixa": visao_caixa(request),
        "modo_demo": request.app.state.settings.modo_demo,
    }


templates = Jinja2Templates(directory=APP_DIR / "templates", context_processors=[_contexto])
templates.env.globals["icone"] = icone
templates.env.globals["estatico"] = estatico
templates.env.filters["brl"] = formatar_brl
templates.env.filters["valor_br"] = lambda centavos: formatar_brl(centavos).replace("R$ ", "")
templates.env.filters["pct"] = formatar_percentual
templates.env.filters["pp"] = lambda valor, casas=1: formatar_percentual(
    valor, casas, sinal=True
).replace("%", " pp")
templates.env.filters["preco"] = formatar_preco
templates.env.filters["qtd"] = formatar_quantidade
templates.env.filters["reais"] = lambda centavos: f"{centavos // 100:,}".replace(",", ".")
templates.env.filters["data_br"] = _data_br
templates.env.filters["hora_local"] = _hora_local

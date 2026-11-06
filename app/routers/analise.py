"""Análise (E6): visão, alocação e aporte, objetivos, regras e snapshot para o Claude."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.db.models import GrupoAlocacao, Objetivo
from app.db.session import obter_sessao
from app.domain import (
    alocacao,
    ativos,
    carteira,
    objetivos,
    regras,
    rentabilidade,
    snapshot,
)
from app.domain.datas import hoje
from app.domain.dinheiro import formatar_brl
from app.domain.formularios import ErroValidacao, Leitor
from app.routers.carteira import contexto_mercado
from app.web import obter_ou_404, resposta_acao, templates

router = APIRouter(prefix="/analise")
Sessao = Annotated[Session, Depends(obter_sessao)]

SECOES = (
    ("", "Visão"),
    ("/alocacao", "Alocação e aporte"),
    ("/objetivos", "Objetivos"),
    ("/regras", "Regras"),
    ("/snapshot", "Snapshot"),
)


def _pagina(request: Request, nome: str, contexto: dict, status: int = 200):
    return templates.TemplateResponse(
        request,
        f"pages/analise_{nome}.html",
        {"secoes": SECOES, "secao": request.url.path.removeprefix("/analise"), **contexto},
        status_code=status,
    )


def _texto_valor(centavos: int | None) -> str:
    return formatar_brl(centavos).replace("R$ ", "") if centavos else ""


def _decimal_texto(valor) -> str:
    return format(valor.normalize(), "f").replace(".", ",") if valor is not None else ""


# ───────── Visão (E6-05, E6-06, E6-12) ─────────


@router.get("")
def visao(request: Request, sessao: Sessao):
    dia = hoje()
    cart = carteira.montar(sessao, dia)
    return _pagina(
        request,
        "visao",
        {
            "rentab": rentabilidade.calcular(sessao, dia),
            "alertas": regras.alertas(sessao, dia, cart),
            "regras_configuradas": regras.obter(sessao).configuradas,
            "situacao": alocacao.alocacao(sessao, dia, cart),
            "reserva": alocacao.reserva(sessao, dia, cart),
            "cart": cart,
            **contexto_mercado(sessao),
        },
    )


# ───────── Alocação e aporte (E6-03, E6-04) ─────────


@router.get("/alocacao")
def pagina_alocacao(request: Request, sessao: Sessao):
    dia = hoje()
    return _pagina(
        request,
        "alocacao",
        {
            "situacao": alocacao.alocacao(sessao, dia),
            "aporte_padrao": _texto_valor(objetivos.premissas(sessao).aporte_mensal),
        },
    )


def _formulario_metas(request, sessao, valores=None, erros=None, status=200):
    if valores is None:
        atuais = alocacao.metas(sessao)
        valores = {}
        for grupo in GrupoAlocacao:
            meta = atuais.get(grupo)
            valores[f"alvo-{grupo.value}"] = _decimal_texto(meta.percentual_alvo) if meta else ""
            valores[f"tolerancia-{grupo.value}"] = (
                _decimal_texto(meta.tolerancia_pp) if meta else ""
            )
    return templates.TemplateResponse(
        request,
        "partials/metas_form.html",
        {
            "valores": valores,
            "erros": erros or {},
            "grupos": list(GrupoAlocacao),
            "sugestao": alocacao.SUGESTAO_MODERADO,
        },  # fmt: skip
        status_code=status,
    )


@router.get("/alocacao/metas")
def editar_metas(request: Request, sessao: Sessao):
    return _formulario_metas(request, sessao)


@router.post("/alocacao/metas")
async def salvar_metas(request: Request, sessao: Sessao):
    dados = dict(await request.form())
    try:
        alocacao.salvar_metas(sessao, dados)
    except ErroValidacao as erro:
        sessao.rollback()
        return _formulario_metas(request, sessao, dados, erro.erros, status=422)
    sessao.commit()
    return resposta_acao("Alocação-alvo salva.")


@router.get("/alocacao/aporte")
def simular_aporte(request: Request, sessao: Sessao, valor: str = ""):
    leitor = Leitor({"valor": valor})
    centavos = leitor.centavos("valor")
    sugestoes = []
    if not leitor.erros:
        sugestoes = alocacao.sugerir_aporte(
            sessao, centavos, hoje(), regras.obter(sessao).reserva_primeiro
        )
    return templates.TemplateResponse(
        request,
        "partials/aporte_resultado.html",
        {
            "sugestoes": sugestoes,
            "erro": leitor.erros.get("valor"),
            "valor": centavos,
            "tem_metas": bool(alocacao.metas(sessao)),
        },  # fmt: skip
    )


# ───────── Objetivos (E6-09) ─────────


@router.get("/objetivos")
def pagina_objetivos(request: Request, sessao: Sessao):
    dia = hoje()
    cart = carteira.montar(sessao, dia)
    base = alocacao.alocacao(sessao, dia, cart).total
    return _pagina(
        request,
        "objetivos",
        {
            "reserva": alocacao.reserva(sessao, dia, cart),
            "renda": objetivos.renda_passiva(sessao, dia, cart, base),
            "itens": objetivos.progresso(sessao, dia, cart, base),
            "premissas": objetivos.premissas(sessao),
            "base": base,
        },
    )


def _formulario_premissas(request, sessao, valores=None, erros=None, status=200):
    if valores is None:
        p = objetivos.premissas(sessao)
        valores = {
            "premissa_retorno_aa": _decimal_texto(p.retorno_aa),
            "premissa_yield": _decimal_texto(p.yield_aa),
            "aporte_mensal": _texto_valor(p.aporte_mensal),
            "reserva_meses": str(alocacao.reserva(sessao, hoje()).meses),
        }
    return templates.TemplateResponse(
        request, "partials/premissas_form.html", {"valores": valores, "erros": erros or {}},
        status_code=status,
    )  # fmt: skip


@router.get("/objetivos/premissas")
def editar_premissas(request: Request, sessao: Sessao):
    return _formulario_premissas(request, sessao)


@router.post("/objetivos/premissas")
async def salvar_premissas(request: Request, sessao: Sessao):
    dados = dict(await request.form())
    try:
        objetivos.salvar_premissas(sessao, dados)
    except ErroValidacao as erro:
        sessao.rollback()
        return _formulario_premissas(request, sessao, dados, erro.erros, status=422)
    sessao.commit()
    return resposta_acao("Premissas salvas.")


def _formulario_objetivo(request, objetivo, valores=None, erros=None, status=200):
    if valores is None:
        valores = {
            "nome": objetivo.nome if objetivo else "",
            "valor_alvo": _texto_valor(objetivo.valor_alvo_centavos) if objetivo else "",
            "prazo": objetivo.prazo.isoformat() if objetivo and objetivo.prazo else "",
            "aporte_mensal": _texto_valor(objetivo.aporte_mensal_centavos) if objetivo else "",
            "descricao": (objetivo.descricao or "") if objetivo else "",
        }
    return templates.TemplateResponse(
        request, "partials/objetivo_form.html",
        {"objetivo": objetivo, "valores": valores, "erros": erros or {}}, status_code=status,
    )  # fmt: skip


@router.get("/objetivos/novo")
def novo_objetivo(request: Request):
    return _formulario_objetivo(request, None)


@router.get("/objetivos/{objetivo_id}/editar")
def editar_objetivo(request: Request, sessao: Sessao, objetivo_id: int):
    return _formulario_objetivo(request, obter_ou_404(sessao, Objetivo, objetivo_id))


async def _salvar_objetivo(request: Request, sessao: Session, objetivo: Objetivo | None):
    dados = dict(await request.form())
    try:
        salvo = objetivos.salvar(sessao, dados, objetivo)
    except ErroValidacao as erro:
        sessao.rollback()
        return _formulario_objetivo(request, objetivo, dados, erro.erros, status=422)
    sessao.commit()
    return resposta_acao(f"Objetivo “{salvo.nome}” salvo.")


@router.post("/objetivos")
async def criar_objetivo(request: Request, sessao: Sessao):
    return await _salvar_objetivo(request, sessao, None)


@router.post("/objetivos/{objetivo_id}")
async def atualizar_objetivo(request: Request, sessao: Sessao, objetivo_id: int):
    return await _salvar_objetivo(request, sessao, obter_ou_404(sessao, Objetivo, objetivo_id))


@router.delete("/objetivos/{objetivo_id}")
def excluir_objetivo(sessao: Sessao, objetivo_id: int):
    registro = objetivos.excluir(sessao, obter_ou_404(sessao, Objetivo, objetivo_id))
    sessao.commit()
    return resposta_acao(f"{registro.descricao} excluído.", desfazer=f"/desfazer/{registro.id}")


# ───────── Regras (E6-06) ─────────


def _valores_regras(atuais: regras.Regras) -> dict:
    valores = {}
    for nome, valor in atuais.__dict__.items():
        if nome == "transicao_patrimonio":
            valores[nome] = _texto_valor(valor)
        elif nome == "setores_excluidos":
            valores[nome] = ", ".join(valor)
        elif nome == "reserva_primeiro":
            valores[nome] = valor
        elif nome == "transicao_max_por_setor":
            valores[nome] = str(valor) if valor else ""
        else:
            valores[nome] = _decimal_texto(valor)
    return valores


@router.get("/regras")
def pagina_regras(request: Request, sessao: Sessao):
    return _tela_regras(request, sessao)


def _tela_regras(request, sessao, valores=None, erros=None, status=200):
    atuais = regras.obter(sessao)
    return _pagina(
        request,
        "regras",
        {
            "valores": valores or _valores_regras(atuais),
            "erros": erros or {},
            "configuradas": atuais.configuradas,
        },  # fmt: skip
        status,
    )


@router.post("/regras")
async def salvar_regras(request: Request, sessao: Sessao):
    dados = dict(await request.form())
    try:
        novas = regras.ler_formulario(dados)
    except ErroValidacao as erro:
        valores = {**dados, "reserva_primeiro": dados.get("reserva_primeiro") == "1"}
        return _tela_regras(request, sessao, valores, erro.erros, status=422)
    regras.gravar(sessao, novas)
    sessao.commit()
    return resposta_acao("Regras salvas. Os alertas já usam os novos valores.", fechar_drawer=False)


@router.post("/regras/sugestao")
def usar_sugestao(sessao: Sessao):
    regras.gravar(sessao, regras.SUGESTAO_MODERADO)
    sessao.commit()
    return resposta_acao(
        "Regras preenchidas com a sugestão do perfil moderado.", fechar_drawer=False
    )


# ───────── Snapshot e pergunta (E6-07, E6-08) ─────────


@router.get("/snapshot")
def pagina_snapshot(request: Request, sessao: Sessao):
    dados = snapshot.montar(sessao, hoje())
    return _pagina(
        request,
        "snapshot",
        {
            "texto": snapshot.para_markdown(dados),
            "ativos": [(a.ticker, a.ticker) for a in ativos.listar(sessao)],
        },  # fmt: skip
    )


@router.get("/snapshot.md")
def baixar_markdown(sessao: Sessao):
    dia = hoje()
    texto = snapshot.para_markdown(snapshot.montar(sessao, dia))
    return PlainTextResponse(
        texto,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="snapshot-{dia}.md"'},
    )


@router.get("/snapshot.json")
def baixar_json(sessao: Sessao):
    dia = hoje()
    conteudo = json.dumps(
        snapshot.para_json(snapshot.montar(sessao, dia)), ensure_ascii=False, indent=2
    )
    return Response(
        conteudo,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="snapshot-{dia}.json"'},
    )


@router.get("/pergunta")
def pergunta(
    request: Request, sessao: Sessao, ticker: str = "", acao: str = "comprar", valor: str = ""
):
    leitor = Leitor({"ticker": ticker, "valor": valor})
    codigo = ativos.normalizar_ticker(leitor.bruto("ticker"))
    centavos = leitor.centavos("valor", obrigatorio=False)
    erro = None if codigo else "Informe o código do ativo."
    texto = (
        "" if erro else snapshot.pergunta(snapshot.montar(sessao, hoje()), codigo, acao, centavos)
    )
    return templates.TemplateResponse(
        request,
        "partials/pergunta_resultado.html",
        {"texto": texto, "erro": erro or leitor.erros.get("valor")},
    )

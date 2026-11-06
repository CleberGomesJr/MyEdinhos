"""Assistente de cadastro inicial (E2-22): contas e saldos → cartões → carteira."""

import json
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db.models import Cartao, Lancamento, TipoCategoria
from app.db.session import obter_sessao
from app.domain import assistente, ativos, carteira, cartoes, categorias, contas, lancamentos
from app.domain.datas import hoje
from app.domain.dinheiro import formatar_brl
from app.domain.formularios import ErroValidacao
from app.web import eventos_htmx, obter_ou_404, templates

router = APIRouter(prefix="/assistente")
Sessao = Annotated[Session, Depends(obter_sessao)]


@dataclass(frozen=True)
class Passo:
    slug: str
    rotulo: str

    @property
    def href(self) -> str:
        return f"/assistente/{self.slug}"


PASSOS = (
    Passo("contas", "Contas e saldos"),
    Passo("cartoes", "Cartões"),
    Passo("carteira", "Carteira"),
)


def _texto_valor(centavos: int) -> str:
    return formatar_brl(centavos).replace("R$ ", "").replace("−", "-")


def _pagina(request: Request, slug: str, contexto: dict, status: int = 200):
    indice = next(i for i, passo in enumerate(PASSOS) if passo.slug == slug)
    return templates.TemplateResponse(
        request,
        f"pages/assistente_{slug}.html",
        {"passos": PASSOS, "passo": slug, "indice": indice, **contexto},
        status_code=status,
    )


def _ir(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


@router.get("")
def inicio():
    return _ir(PASSOS[0].href)


# ───────── Passo 1: contas e saldos ─────────


@router.get("/contas")
def passo_contas(request: Request, sessao: Sessao):
    return _tela_contas(request, sessao)


def _tela_contas(request, sessao, valores=None, erros=None, status=200):
    ativas = contas.listar(sessao)
    atuais = contas.saldos(sessao, hoje())
    if valores is None:
        valores = {f"saldo-{c.id}": _texto_valor(atuais.get(c.id, 0)) for c in ativas}
    return _pagina(
        request, "contas", {"contas": ativas, "valores": valores, "erros": erros or {}}, status
    )


@router.post("/contas")
async def salvar_contas(request: Request, sessao: Sessao):
    dados = dict(await request.form())
    try:
        assistente.salvar_saldos(sessao, dados, hoje())
    except ErroValidacao as erro:
        sessao.rollback()
        return _tela_contas(request, sessao, dados, erro.erros, status=422)
    sessao.commit()
    return _ir(PASSOS[1].href)


# ───────── Passo 2: cartões, faturas e parcelamentos ─────────


@router.get("/cartoes")
def passo_cartoes(request: Request, sessao: Sessao):
    return _tela_cartoes(request, sessao)


def _tela_cartoes(request, sessao, valores=None, erros=None, status=200):
    dia = hoje()
    paineis = []
    for cartao in cartoes.listar(sessao):
        faturas = assistente.faturas_em_aberto(sessao, cartao, dia)
        paineis.append(
            {
                "cartao": cartao,
                "faturas": cartoes.resumir(sessao, faturas, dia),
                "parcelamentos": assistente.parcelamentos_em_andamento(sessao, cartao, dia),
            }
        )
    sessao.commit()  # as faturas em aberto passam a existir (recebem o ajuste ao salvar)
    if valores is None:
        valores = {
            f"fatura-{item.fatura.id}": _texto_valor(item.total) if item.total else ""
            for painel in paineis
            for item in painel["faturas"]
        }
    contexto = {
        "paineis": paineis,
        "valores": valores,
        "erros": erros or {},
        "tem_contas": bool(contas.listar(sessao)),
    }
    return _pagina(request, "cartoes", contexto, status)


@router.post("/cartoes")
async def salvar_cartoes(request: Request, sessao: Sessao):
    dados = dict(await request.form())
    try:
        assistente.salvar_faturas(sessao, dados, hoje())
    except ErroValidacao as erro:
        sessao.rollback()
        return _tela_cartoes(request, sessao, dados, erro.erros, status=422)
    sessao.commit()
    return _ir(PASSOS[2].href)


def _evento_parcelamentos(mensagem: str, **opcoes) -> dict[str, str]:
    """Toast + evento que recarrega só a lista de parcelamentos (sem apagar o que já foi
    digitado nos totais das faturas)."""
    eventos = json.loads(eventos_htmx(mensagem, **opcoes)["HX-Trigger"])
    eventos["parcelamentos"] = True
    return {"HX-Trigger": json.dumps(eventos)}


def _formulario_parcelamento(request, sessao, cartao, valores=None, erros=None, status=200):
    return templates.TemplateResponse(
        request,
        "partials/parcelamento_form.html",
        {
            "cartao": cartao,
            "valores": valores or {"parcela_atual": "1"},
            "erros": erros or {},
            "categorias": categorias.opcoes(sessao, TipoCategoria.SAIDA),
        },
        status_code=status,
    )


@router.get("/cartoes/{cartao_id}/parcelamentos/novo")
def novo_parcelamento(request: Request, sessao: Sessao, cartao_id: int):
    return _formulario_parcelamento(request, sessao, obter_ou_404(sessao, Cartao, cartao_id))


@router.post("/cartoes/{cartao_id}/parcelamentos")
async def criar_parcelamento(request: Request, sessao: Sessao, cartao_id: int):
    cartao = obter_ou_404(sessao, Cartao, cartao_id)
    dados = dict(await request.form())
    try:
        criadas = assistente.adicionar_parcelamento(sessao, cartao, dados, hoje())
    except ErroValidacao as erro:
        sessao.rollback()
        return _formulario_parcelamento(request, sessao, cartao, dados, erro.erros, 422)
    sessao.commit()
    mensagem = f"“{criadas[0].descricao}”: {len(criadas)} parcela(s) nas próximas faturas."
    return Response(status_code=204, headers=_evento_parcelamentos(mensagem, fechar_drawer=True))


@router.delete("/parcelamentos/{lancamento_id}")
def excluir_parcelamento(sessao: Sessao, lancamento_id: int):
    parcela = obter_ou_404(sessao, Lancamento, lancamento_id)
    registro = lancamentos.excluir(sessao, parcela, escopo="proximas")
    sessao.commit()
    return Response(
        status_code=204,
        headers=_evento_parcelamentos(
            f"{registro.descricao} excluído.", desfazer=f"/desfazer/{registro.id}"
        ),
    )


# ───────── Passo 3: carteira (posição inicial em lote, E4-15) ─────────


@router.get("/carteira")
def passo_carteira(request: Request, sessao: Sessao):
    return _tela_carteira(request, sessao)


def _tela_carteira(request, sessao, valores=None, erros=None, status=200):
    contexto = {
        "valores": valores or {"data": hoje().isoformat(), "linhas": ""},
        "erros": erros or {},
        "ativos_na_carteira": len(
            [p for p in carteira.posicoes(sessao, hoje()).values() if p.aberta]
        ),
    }
    return _pagina(request, "carteira", contexto, status)


@router.post("/carteira")
async def salvar_carteira(request: Request, sessao: Sessao):
    dados = dict(await request.form())
    try:
        ativos.importar_posicao_inicial(sessao, dados)
    except ErroValidacao as erro:
        sessao.rollback()
        return _tela_carteira(request, sessao, dados, erro.erros, status=422)
    assistente.encerrar(sessao, "concluido")
    sessao.commit()
    return _ir("/carteira")


@router.post("/concluir")
def concluir(sessao: Sessao):
    assistente.encerrar(sessao, "concluido")
    sessao.commit()
    return _ir("/")


@router.post("/dispensar")
def dispensar(sessao: Sessao):
    """ "Agora não" na Visão geral: o convite some; o assistente continua na busca (Ctrl+K)."""
    assistente.encerrar(sessao, "dispensado")
    sessao.commit()
    return Response(
        headers=eventos_htmx("Tudo bem. O cadastro inicial continua na busca (Ctrl+K).")
    )

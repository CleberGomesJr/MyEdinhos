"""Proventos ⭐ (E4-10, E4-13, E4-14, E5-12)."""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db.models import ProventoAnunciado
from app.db.session import obter_sessao
from app.domain import carteira, proventos
from app.domain.datas import hoje
from app.domain.dinheiro import formatar_brl
from app.domain.formularios import Leitor
from app.routers.carteira import contexto_mercado
from app.web import mes_da_requisicao, obter_ou_404, resposta_acao, templates

router = APIRouter(prefix="/proventos")
Sessao = Annotated[Session, Depends(obter_sessao)]


@router.get("")
def pagina(request: Request, sessao: Sessao):
    mes = mes_da_requisicao(request)
    dia = hoje()
    inicio_12m = dia - timedelta(days=365)
    cart = carteira.montar(sessao, dia)
    por_mes = proventos.por_mes(sessao, mes)
    a_receber, sugestoes = proventos.anunciados(sessao, dia)
    por_unidade = proventos.proventos_por_unidade_12m(sessao, dia)
    return templates.TemplateResponse(
        request,
        "pages/proventos.html",
        {
            "recebidos_mes": proventos.recebidos(sessao, mes.primeiro_dia, mes.ultimo_dia),
            "cart": cart,
            "meta": proventos.meta(sessao, cart.proventos_12m, cart.yield_on_cost),
            "grafico_mes": {
                "rotulos": [m.rotulo_curto for m, _ in por_mes],
                "formato": "brl",
                "series": [
                    {"nome": "Proventos", "cor": "entrada", "valores": [v for _, v in por_mes]}
                ],
            },
            "tem_proventos": any(v for _, v in por_mes),
            "ranking": proventos.ranking_ativos(sessao, inicio_12m, dia),
            "por_classe": proventos.por_classe(sessao, inicio_12m, dia),
            "a_receber": a_receber,
            "sugestoes": sugestoes,
            "rendimentos": sorted(
                (
                    (linha, proventos.dividend_yield(linha, por_unidade))
                    for linha in cart.linhas
                    if linha.proventos_12m or por_unidade.get(linha.ativo.id)
                ),
                key=lambda item: -(item[0].proventos_12m),
            ),
            "hoje": dia,
            **contexto_mercado(sessao),
        },
    )


def _formulario_meta(request, sessao, valores=None, erros=None, status=200):
    if valores is None:
        atual = proventos.meta(sessao, 0, None)
        valores = {"meta": formatar_brl(atual.mensal).replace("R$ ", "") if atual else ""}
    return templates.TemplateResponse(
        request, "partials/meta_form.html", {"valores": valores, "erros": erros or {}},
        status_code=status,
    )  # fmt: skip


@router.get("/meta")
def editar_meta(request: Request, sessao: Sessao):
    return _formulario_meta(request, sessao)


@router.post("/meta")
async def salvar_meta(request: Request, sessao: Sessao):
    dados = dict(await request.form())
    leitor = Leitor(dados)
    valor = leitor.centavos("meta", obrigatorio=False)
    if leitor.erros:
        return _formulario_meta(request, sessao, dados, leitor.erros, status=422)
    proventos.definir_meta(sessao, valor)
    sessao.commit()
    if not valor:
        return resposta_acao("Meta de renda passiva removida.")
    return resposta_acao(f"Meta de {formatar_brl(valor)} por mês salva.")


@router.post("/anunciados/{anuncio_id}/ignorar")
def ignorar(sessao: Sessao, anuncio_id: int):
    anuncio = obter_ou_404(sessao, ProventoAnunciado, anuncio_id)
    proventos.ignorar(sessao, anuncio)
    sessao.commit()
    return resposta_acao(f"Sugestão de {anuncio.ativo.ticker} ignorada.", fechar_drawer=False)

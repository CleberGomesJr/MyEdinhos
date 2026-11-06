"""Mercado (E5-07, E5-10): atualização de cotações, acompanhamento e índices."""

import threading
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db.session import obter_sessao
from app.domain import carteira, mercado
from app.domain.datas import hoje
from app.routers.carteira import contexto_mercado
from app.web import resposta_acao, templates

router = APIRouter(prefix="/mercado")
_atualizando = threading.Lock()  # duas abas abertas não atualizam ao mesmo tempo
Sessao = Annotated[Session, Depends(obter_sessao)]


@router.get("")
def pagina(request: Request, sessao: Sessao):
    dia = hoje()
    com_posicao = {aid for aid, pos in carteira.posicoes(sessao, dia).items() if pos.aberta}
    requisicoes = mercado.requisicoes_brapi(sessao, dia)
    return templates.TemplateResponse(
        request,
        "pages/mercado.html",
        {
            "itens": mercado.acompanhados(sessao, dia, com_posicao),
            "indices": mercado.indices(sessao, dia),
            "titulos": mercado.titulos_tesouro(sessao),
            "tem_token": bool(request.app.state.settings.brapi_token),
            "requisicoes": requisicoes,
            "limite_brapi": mercado.LIMITE_BRAPI,
            "alerta_brapi": requisicoes >= mercado.LIMITE_BRAPI * mercado.ALERTA_BRAPI,
            **contexto_mercado(sessao),
        },
    )


@router.post("/atualizar")
def atualizar(request: Request, sessao: Sessao):
    if request.app.state.settings.modo_demo:
        return resposta_acao(
            "Na demonstração as cotações são fictícias.", tipo="info", fechar_drawer=False
        )
    if not _atualizando.acquire(blocking=False):
        return resposta_acao(
            "As cotações já estão sendo atualizadas.", tipo="info", fechar_drawer=False
        )
    try:
        dia = hoje()
        fontes = mercado.fontes_configuradas(sessao, request.app.state.settings.brapi_token, dia)
        relatorio = mercado.atualizar(sessao, fontes, dia)
        sessao.commit()
    finally:
        _atualizando.release()
    mensagem = f"Cotações atualizadas: {len(relatorio.atualizados)} ativo(s)."
    if relatorio.erros:
        mensagem += f" {len(relatorio.erros)} com erro — veja em Mercado."
    return resposta_acao(mensagem, tipo="erro" if relatorio.erros else "ok", fechar_drawer=False)


@router.post("/automatico")
async def automatico(request: Request, sessao: Sessao):
    ligado = (await request.form()).get("automatico") == "1"
    mercado.definir_automatico(sessao, ligado)
    sessao.commit()
    texto = "ligada" if ligado else "desligada"
    return resposta_acao(f"Atualização automática ao abrir {texto}.", fechar_drawer=False)

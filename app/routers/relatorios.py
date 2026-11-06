"""Relatórios do ano (E3-09, E3-10)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db.session import obter_sessao
from app.domain import relatorios
from app.domain.competencia import Competencia
from app.web import mes_da_requisicao, templates, visao_caixa

router = APIRouter(prefix="/relatorios")
Sessao = Annotated[Session, Depends(obter_sessao)]


@router.get("")
def pagina(request: Request, sessao: Sessao, ano: int | None = None):
    ano = ano if ano and 1900 < ano < 3000 else mes_da_requisicao(request).ano
    caixa = visao_caixa(request)
    resumo = relatorios.resumo_anual(sessao, ano, caixa, ate=Competencia.atual())
    return templates.TemplateResponse(
        request,
        "pages/relatorios.html",
        {
            "ano": ano,
            "resumo": resumo,
            "anterior": relatorios.resumo_anual(sessao, ano - 1, caixa).total,
            "mapa": relatorios.mapa_categorias(sessao, ano),
            "meses": relatorios.meses_do_ano(ano),
            "mes_atual": Competencia.atual(),
        },
    )

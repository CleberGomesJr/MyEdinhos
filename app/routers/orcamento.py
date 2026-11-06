"""Orçamento por categoria (E3-07)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db.models import Categoria
from app.db.session import obter_sessao
from app.domain import orcamentos
from app.domain.competencia import Competencia
from app.domain.datas import hoje
from app.domain.dinheiro import formatar_brl
from app.domain.formularios import ErroValidacao
from app.domain.recorrencias import gerar_pendentes, limite_geracao
from app.domain.resumos import meses_ate, saidas_por_principal
from app.web import mes_da_requisicao, obter_ou_404, resposta_acao, templates

router = APIRouter(prefix="/orcamento")
Sessao = Annotated[Session, Depends(obter_sessao)]


@router.get("")
def pagina(request: Request, sessao: Sessao):
    mes = mes_da_requisicao(request)
    gerar_pendentes(sessao, limite_geracao(mes, hoje()))
    sessao.commit()
    linhas = orcamentos.listar(sessao, mes)
    return templates.TemplateResponse(
        request,
        "pages/orcamento.html",
        {
            "linhas": linhas,
            "total": orcamentos.total(linhas),
            "estouradas": sum(1 for linha in linhas if linha.estado == "critico"),
        },
    )


def _media_3_meses(sessao: Session, categoria: Categoria, mes: Competencia) -> int:
    meses = meses_ate(mes.anterior(), 3)
    soma = sum(dict(saidas_por_principal(sessao, m)).get(categoria.id, 0) for m in meses)
    return soma // 3


def _formulario(request, sessao, categoria, mes, valores=None, erros=None, status=200):
    linha = next(
        (ln for ln in orcamentos.listar(sessao, mes) if ln.categoria.id == categoria.id),
        orcamentos.Linha(categoria, 0, None),
    )
    if valores is None:
        valores = {"limite": formatar_brl(linha.limite).replace("R$ ", "") if linha.limite else ""}
    return templates.TemplateResponse(
        request,
        "partials/orcamento_form.html",
        {
            "categoria": categoria,
            "mes_form": mes,
            "linha": linha,
            "media": _media_3_meses(sessao, categoria, mes),
            "valores": valores,
            "erros": erros or {},
        },
        status_code=status,
    )


@router.get("/{categoria_id}/editar")
def editar(request: Request, sessao: Sessao, categoria_id: int):
    categoria = obter_ou_404(sessao, Categoria, categoria_id)
    return _formulario(request, sessao, categoria, mes_da_requisicao(request))


@router.post("/{categoria_id}")
async def salvar(request: Request, sessao: Sessao, categoria_id: int):
    categoria = obter_ou_404(sessao, Categoria, categoria_id)
    dados = dict(await request.form())
    try:
        mes = Competencia.de_texto(str(dados.get("mes", "")))
    except ValueError:
        mes = mes_da_requisicao(request)
    try:
        limite = orcamentos.definir(sessao, categoria, mes, dados)
    except ErroValidacao as erro:
        sessao.rollback()
        return _formulario(request, sessao, categoria, mes, dados, erro.erros, status=422)
    sessao.commit()
    if limite is None:
        return resposta_acao(f"{categoria.nome}: sem limite a partir de {mes.rotulo_curto}.")
    return resposta_acao(
        f"{categoria.nome}: limite de {formatar_brl(limite)} a partir de {mes.rotulo_curto}."
    )

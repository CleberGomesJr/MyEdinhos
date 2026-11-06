from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Categoria, TipoCategoria
from app.db.session import obter_sessao
from app.domain import categorias
from app.domain.formularios import ErroValidacao
from app.web import obter_ou_404, resposta_acao, templates

router = APIRouter(prefix="/categorias")
Sessao = Annotated[Session, Depends(obter_sessao)]


@router.get("")
def pagina(request: Request, sessao: Sessao):
    return templates.TemplateResponse(
        request,
        "pages/categorias.html",
        {
            "saidas": categorias.arvore(sessao, TipoCategoria.SAIDA),
            "entradas": categorias.arvore(sessao, TipoCategoria.ENTRADA),
            "arquivadas": categorias.arquivadas(sessao),
        },
    )


def _pais(sessao: Session, tipo: TipoCategoria, categoria: Categoria | None) -> list[tuple]:
    """Categorias que podem ser "principal" de outra: ativas, de nível 1 e não ela mesma."""
    consulta = (
        select(Categoria)
        .where(Categoria.tipo == tipo, Categoria.pai_id.is_(None), Categoria.ativa)
        .order_by(Categoria.nome)
    )
    return [("", "Nenhuma (categoria principal)")] + [
        (pai.id, pai.nome)
        for pai in sessao.scalars(consulta)
        if categoria is None or pai.id != categoria.id
    ]


def _formulario(request, sessao, categoria, valores=None, erros=None, status=200):
    if valores is None:
        tipo = request.query_params.get("tipo", "saida")
        valores = {
            "nome": categoria.nome if categoria else "",
            "tipo": categoria.tipo.value if categoria else tipo,
            "pai_id": str(categoria.pai_id or "") if categoria else "",
            "cor_slot": str(categoria.cor_slot) if categoria else "1",
            "icone": categoria.icone if categoria else "circle-ellipsis",
        }
    em_uso = bool(categoria and categorias.uso(sessao, categoria))
    return templates.TemplateResponse(
        request,
        "partials/categoria_form.html",
        {
            "categoria": categoria,
            "valores": valores,
            "erros": erros or {},
            "pais_saida": _pais(sessao, TipoCategoria.SAIDA, categoria),
            "pais_entrada": _pais(sessao, TipoCategoria.ENTRADA, categoria),
            "icones": categorias.ICONES,
            "tipo_travado": em_uso,
            "tem_subcategorias": bool(categoria and categoria.subcategorias),
        },
        status_code=status,
    )


@router.get("/nova")
def nova(request: Request, sessao: Sessao):
    return _formulario(request, sessao, None)


@router.get("/{categoria_id}/editar")
def editar(request: Request, sessao: Sessao, categoria_id: int):
    return _formulario(request, sessao, obter_ou_404(sessao, Categoria, categoria_id))


async def _salvar(request: Request, sessao: Session, categoria: Categoria | None):
    dados = dict(await request.form())
    if categoria is not None and "tipo" not in dados:  # campo travado (desabilitado) no form
        dados["tipo"] = categoria.tipo.value
    try:
        salva = categorias.salvar(sessao, dados, categoria)
    except ErroValidacao as erro:
        sessao.rollback()
        return _formulario(request, sessao, categoria, dados, erro.erros, status=422)
    sessao.commit()
    return resposta_acao(f"Categoria “{salva.nome}” salva.")


@router.post("")
async def criar(request: Request, sessao: Sessao):
    return await _salvar(request, sessao, None)


@router.post("/{categoria_id}")
async def atualizar(request: Request, sessao: Sessao, categoria_id: int):
    return await _salvar(request, sessao, obter_ou_404(sessao, Categoria, categoria_id))


@router.post("/{categoria_id}/excluir")
def excluir(sessao: Sessao, categoria_id: int):
    categoria = obter_ou_404(sessao, Categoria, categoria_id)
    nome = categoria.nome
    registro = categorias.excluir(sessao, categoria)
    sessao.commit()
    if registro is None:
        return resposta_acao(
            f"“{nome}” foi arquivada: ela tem lançamentos ou subcategorias.", tipo="info"
        )
    return resposta_acao(f"Categoria “{nome}” excluída.", desfazer=f"/desfazer/{registro.id}")


@router.post("/{categoria_id}/reativar")
def reativar(sessao: Sessao, categoria_id: int):
    categoria = obter_ou_404(sessao, Categoria, categoria_id)
    categorias.reativar(sessao, categoria)
    sessao.commit()
    return resposta_acao(f"Categoria “{categoria.nome}” reativada.")

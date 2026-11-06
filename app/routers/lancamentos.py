from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.db.models import Lancamento, Recorrencia, TipoCategoria, TipoLancamento
from app.db.session import obter_sessao
from app.domain import cartoes, categorias, contas, exportacao, lancamentos, recorrencias
from app.domain.competencia import Competencia
from app.domain.datas import hoje
from app.domain.formularios import ErroValidacao
from app.domain.lancamentos import Filtros, Situacao
from app.web import (
    eventos_htmx,
    mes_da_requisicao,
    obter_ou_404,
    resposta_acao,
    templates,
)

router = APIRouter(prefix="/lancamentos")
Sessao = Annotated[Session, Depends(obter_sessao)]


def _texto_valor(centavos: int) -> str:
    reais, resto = divmod(centavos, 100)
    return f"{reais:,}".replace(",", ".") + f",{resto:02d}"


# ───────── Lista do mês ─────────


@router.get("")
def pagina(request: Request, sessao: Sessao):
    mes = mes_da_requisicao(request)
    dia = hoje()
    recorrencias.gerar_pendentes(sessao, recorrencias.limite_geracao(mes, dia))
    sessao.commit()
    filtros = Filtros.de_parametros(request.query_params)
    itens = lancamentos.listar(sessao, mes, filtros, dia)
    return templates.TemplateResponse(
        request,
        "pages/lancamentos.html",
        {
            "itens": itens,
            "totais": lancamentos.totais(itens),
            "filtros": filtros,
            "hoje": dia,
            "atrasado": lancamentos.atrasado,
            "atrasados": lancamentos.contar_atrasados(sessao, dia),
            "contas": contas.listar(sessao, incluir_arquivadas=True),
            "cartoes": cartoes.listar(sessao, incluir_arquivados=True),
            "categorias_saida": categorias.opcoes(sessao, TipoCategoria.SAIDA),
            "categorias_entrada": categorias.opcoes(sessao, TipoCategoria.ENTRADA),
            "tipos": list(TipoLancamento),
            "situacoes": list(Situacao),
            "exportar_url": "/lancamentos/exportar.csv?"
            + urlencode({**request.query_params, "mes": str(mes)}),
        },
    )


@router.get("/exportar.csv")
def exportar(request: Request, sessao: Sessao):
    """E3-11: o que está na lista (mês e filtros) em CSV."""
    mes = mes_da_requisicao(request)
    filtros = Filtros.de_parametros(request.query_params)
    itens = lancamentos.listar(sessao, mes, filtros, hoje())
    nome = (
        "lancamentos-atrasados" if filtros.situacao == Situacao.ATRASADOS else f"lancamentos-{mes}"
    )
    return Response(
        exportacao.csv_lancamentos(itens),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}.csv"'},
    )


# ───────── Formulário (drawer) ─────────


def _valores_novos(sessao: Session, mes: Competencia, anteriores: dict | None = None) -> dict:
    """Padrões do formulário novo; `anteriores` mantém tipo/conta/data/categoria no
    "Salvar e adicionar outro"."""
    dia = hoje()
    data = dia if Competencia.de_data(dia) == mes else mes.primeiro_dia
    ativas = contas.listar(sessao)
    ativos = cartoes.listar(sessao)
    valores = {
        "tipo": "saida",
        "valor": "",
        "descricao": "",
        "data": data.isoformat(),
        "conta_id": str(ativas[0].id) if ativas else "",
        "meio": "conta",
        "cartao_id": str(ativos[0].id) if ativos else "",
        "conta_destino_id": "",
        "categoria_id": "",
        "pago": "1",
        "vencimento": "",
        "repeticao": "nao",
        "parcelas": "2",
        "retroativo": "",
        "parcelas_pagas": "",
        "observacao": "",
    }
    for campo in (
        "tipo",
        "data",
        "conta_id",
        "meio",
        "cartao_id",
        "conta_destino_id",
        "categoria_id",
    ):
        if anteriores and anteriores.get(campo):
            valores[campo] = anteriores[campo]
    return valores


def _valores_de(lancamento: Lancamento) -> dict:
    return {
        "tipo": lancamento.tipo.value,
        "valor": _texto_valor(lancamento.valor_centavos),
        "descricao": lancamento.descricao,
        "data": lancamento.data.isoformat(),
        "conta_id": str(lancamento.conta_id or ""),
        "meio": "cartao" if lancamento.cartao_id else "conta",
        "cartao_id": str(lancamento.cartao_id or ""),
        "conta_destino_id": str(lancamento.conta_destino_id or ""),
        "categoria_id": str(lancamento.categoria_id or ""),
        "pago": "1" if lancamento.pago else "",
        "vencimento": lancamento.vencimento.isoformat() if lancamento.vencimento else "",
        "observacao": lancamento.observacao or "",
    }


def _formulario(
    request, sessao, lancamento=None, valores=None, erros=None, status=200, headers=None
):
    if valores is None:
        if lancamento:
            valores = _valores_de(lancamento)
        else:
            valores = _valores_novos(sessao, mes_da_requisicao(request))
    manter = lancamento.categoria_id if lancamento else None
    opcoes_contas = [
        (conta.id, conta.nome)
        for conta in contas.listar(sessao, incluir_arquivadas=True)
        if conta.ativa
        or (lancamento and conta.id in (lancamento.conta_id, lancamento.conta_destino_id))
    ]
    opcoes_cartoes = [
        (cartao.id, cartao.nome)
        for cartao in cartoes.listar(sessao, incluir_arquivados=True)
        if cartao.ativo or (lancamento and cartao.id == lancamento.cartao_id)
    ]
    return templates.TemplateResponse(
        request,
        "partials/lancamento_form.html",
        {
            "lancamento": lancamento,
            "valores": valores,
            "erros": erros or {},
            "contas": opcoes_contas,
            "cartoes": opcoes_cartoes,
            "categorias_saida": categorias.opcoes(sessao, TipoCategoria.SAIDA, manter),
            "categorias_entrada": categorias.opcoes(sessao, TipoCategoria.ENTRADA, manter),
            "repeticoes": lancamentos.REPETICOES,
        },
        status_code=status,
        headers=headers,
    )


@router.get("/novo")
def novo(request: Request, sessao: Sessao):
    return _formulario(request, sessao)


@router.get("/{lancamento_id}/editar")
def editar(request: Request, sessao: Sessao, lancamento_id: int):
    return _formulario(request, sessao, obter_ou_404(sessao, Lancamento, lancamento_id))


def _mensagem_criados(criados: list[Lancamento]) -> str:
    primeiro = criados[0]
    if len(criados) > 1:
        pagas = sum(1 for item in criados if item.retroativo)
        detalhe = f" ({pagas} já paga{'s' if pagas > 1 else ''}, no histórico)" if pagas else ""
        return f"{len(criados)} parcelas de “{primeiro.descricao}” criadas{detalhe}."
    if primeiro.recorrencia_id:
        frequencia = primeiro.recorrencia.frequencia.rotulo.lower()
        return f"“{primeiro.descricao}” salvo e repetido {frequencia}."
    return f"“{primeiro.descricao}” salvo."


@router.post("")
async def criar(request: Request, sessao: Sessao):
    dados = dict(await request.form())
    try:
        criados = lancamentos.salvar(sessao, dados)
    except ErroValidacao as erro:
        sessao.rollback()
        return _formulario(request, sessao, None, dados, erro.erros, status=422)
    sessao.commit()
    mensagem = _mensagem_criados(criados)
    if dados.get("continuar"):
        mes = Competencia.de_data(criados[0].data)
        return _formulario(
            request,
            sessao,
            valores=_valores_novos(sessao, mes, dados),
            headers=eventos_htmx(mensagem, atualizar=True),
        )
    return resposta_acao(mensagem)


@router.post("/{lancamento_id}")
async def atualizar(request: Request, sessao: Sessao, lancamento_id: int):
    lancamento = obter_ou_404(sessao, Lancamento, lancamento_id)
    dados = dict(await request.form())
    try:
        lancamentos.salvar(sessao, dados, lancamento)
    except ErroValidacao as erro:
        sessao.rollback()
        return _formulario(request, sessao, lancamento, dados, erro.erros, status=422)
    sessao.commit()
    return resposta_acao(f"“{lancamento.descricao}” atualizado.")


@router.delete("/{lancamento_id}")
def excluir(sessao: Sessao, lancamento_id: int, escopo: str = "este"):
    lancamento = obter_ou_404(sessao, Lancamento, lancamento_id)
    registro = lancamentos.excluir(sessao, lancamento, escopo)
    sessao.commit()
    return resposta_acao(f"{registro.descricao} excluído.", desfazer=f"/desfazer/{registro.id}")


@router.post("/{lancamento_id}/pagar")
def pagar(sessao: Sessao, lancamento_id: int):
    lancamento = obter_ou_404(sessao, Lancamento, lancamento_id)
    lancamentos.marcar_pago(sessao, lancamento)
    sessao.commit()
    return resposta_acao(f"“{lancamento.descricao}” marcado como pago.", fechar_drawer=False)


# ───────── Recorrências (E2-10) ─────────


@router.get("/recorrencias")
def lista_recorrencias(request: Request, sessao: Sessao):
    dia = hoje()
    regras = recorrencias.listar(sessao)
    return templates.TemplateResponse(
        request,
        "pages/recorrencias.html",
        {
            "regras": regras,
            "proxima": {r.id: recorrencias.proxima(r, dia) for r in regras if r.ativa},
        },
    )


def _formulario_regra(request, sessao, regra: Recorrencia, valores=None, erros=None, status=200):
    if valores is None:
        valores = {
            "descricao": regra.descricao,
            "valor": _texto_valor(regra.valor_centavos),
            "categoria_id": str(regra.categoria_id),
        }
    tipo = TipoCategoria(regra.tipo.value)
    return templates.TemplateResponse(
        request,
        "partials/recorrencia_form.html",
        {
            "regra": regra,
            "valores": valores,
            "erros": erros or {},
            "categorias": categorias.opcoes(sessao, tipo, regra.categoria_id),
        },
        status_code=status,
    )


@router.get("/recorrencias/{regra_id}/editar")
def editar_recorrencia(request: Request, sessao: Sessao, regra_id: int):
    return _formulario_regra(request, sessao, obter_ou_404(sessao, Recorrencia, regra_id))


@router.post("/recorrencias/{regra_id}")
async def atualizar_recorrencia(request: Request, sessao: Sessao, regra_id: int):
    regra = obter_ou_404(sessao, Recorrencia, regra_id)
    dados = dict(await request.form())
    try:
        mudados = recorrencias.atualizar(sessao, regra, dados, hoje())
    except ErroValidacao as erro:
        sessao.rollback()
        return _formulario_regra(request, sessao, regra, dados, erro.erros, status=422)
    sessao.commit()
    return resposta_acao(
        f"Recorrência atualizada ({mudados} lançamento(s) pendente(s) ajustado(s))."
    )


@router.post("/recorrencias/{regra_id}/encerrar")
def encerrar_recorrencia(sessao: Sessao, regra_id: int):
    regra = obter_ou_404(sessao, Recorrencia, regra_id)
    removidos = recorrencias.encerrar(sessao, regra, hoje())
    sessao.commit()
    return resposta_acao(
        f"“{regra.descricao}” encerrada; {removidos} lançamento(s) futuro(s) removido(s)."
    )

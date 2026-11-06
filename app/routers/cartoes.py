"""Cartões de crédito, faturas e pagamentos (E2-15, E2-18, E2-19)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Cartao, CompraNoFechamento, Fatura, Lancamento
from app.db.session import obter_sessao
from app.domain import cartoes, contas
from app.domain.competencia import Competencia
from app.domain.datas import hoje
from app.domain.dinheiro import formatar_brl
from app.domain.formularios import ErroValidacao
from app.domain.recorrencias import gerar_pendentes, limite_geracao
from app.web import mes_da_requisicao, obter_ou_404, resposta_acao, templates

router = APIRouter(prefix="/cartoes")
Sessao = Annotated[Session, Depends(obter_sessao)]


def _texto_valor(centavos: int) -> str:
    return formatar_brl(centavos).replace("R$ ", "")


def _opcoes_contas(sessao: Session, manter_id: int | None) -> list[tuple[int, str]]:
    """Contas ativas, mais a arquivada que já está em uso no registro sendo editado."""
    return [
        (conta.id, conta.nome)
        for conta in contas.listar(sessao, incluir_arquivadas=True)
        if conta.ativa or conta.id == manter_id
    ]


def _fatura_do_mes(sessao: Session, cartao: Cartao, mes: Competencia) -> Fatura:
    """A fatura que vence no mês exibido. Se ainda não existe, um objeto só para exibir
    (sem gravar: navegar pelos meses não cria faturas vazias)."""
    existente = sessao.scalar(
        select(Fatura).where(Fatura.cartao_id == cartao.id, Fatura.referencia == str(mes))
    )
    if existente:
        return existente
    fechamento, vencimento = cartoes.datas_da_fatura(cartao, mes)
    return Fatura(
        cartao_id=cartao.id,
        referencia=str(mes),
        data_fechamento=fechamento,
        data_vencimento=vencimento,
    )


@router.get("")
def pagina(request: Request, sessao: Sessao):
    mes = mes_da_requisicao(request)
    dia = hoje()
    gerar_pendentes(sessao, limite_geracao(mes, dia))
    paineis = []
    for cartao in cartoes.listar(sessao):
        fatura = _fatura_do_mes(sessao, cartao, mes)
        itens = cartoes.lancamentos_da_fatura(sessao, fatura) if fatura.id else []
        historico = [
            item
            for item in cartoes.resumir(sessao, cartoes.faturas_do_cartao(sessao, cartao), dia)
            if item.total or item.pago
        ]
        paineis.append(
            {
                "cartao": cartao,
                "limite": cartoes.limite(sessao, cartao, dia),
                "resumo": cartoes.resumo(sessao, fatura, dia),
                "compras": [item for item in itens if not item.pagamento_de_fatura],
                "pagamentos": [item for item in itens if item.pagamento_de_fatura],
                "historico": list(reversed(historico))[:12],
            }
        )
    sessao.commit()
    return templates.TemplateResponse(
        request,
        "pages/cartoes.html",
        {
            "paineis": paineis,
            "arquivados": [
                c for c in cartoes.listar(sessao, incluir_arquivados=True) if not c.ativo
            ],
            "tem_contas": bool(contas.listar(sessao)),
        },
    )


# ───────── Cadastro do cartão (E2-15) ─────────


def _formulario(request, sessao, cartao: Cartao | None, valores=None, erros=None, status=200):
    ativas = contas.listar(sessao)
    if valores is None:
        valores = {
            "nome": cartao.nome if cartao else "",
            "bandeira": (cartao.bandeira or "") if cartao else "",
            "limite": _texto_valor(cartao.limite_centavos) if cartao else "",
            "dia_fechamento": str(cartao.dia_fechamento) if cartao else "",
            "dia_vencimento": str(cartao.dia_vencimento) if cartao else "",
            "compra_no_fechamento": (
                cartao.compra_no_fechamento.value
                if cartao
                else CompraNoFechamento.FATURA_ATUAL.value
            ),
            "conta_pagamento_id": str(
                cartao.conta_pagamento_id if cartao else (ativas[0].id if ativas else "")
            ),
        }
    opcoes_contas = _opcoes_contas(sessao, cartao.conta_pagamento_id if cartao else None)
    return templates.TemplateResponse(
        request,
        "partials/cartao_form.html",
        {
            "cartao": cartao,
            "valores": valores,
            "erros": erros or {},
            "contas": opcoes_contas,
            "regras": [(regra.value, regra.rotulo) for regra in CompraNoFechamento],
        },
        status_code=status,
    )


@router.get("/novo")
def novo(request: Request, sessao: Sessao):
    return _formulario(request, sessao, None)


@router.get("/{cartao_id}/editar")
def editar(request: Request, sessao: Sessao, cartao_id: int):
    return _formulario(request, sessao, obter_ou_404(sessao, Cartao, cartao_id))


async def _salvar(request: Request, sessao: Session, cartao: Cartao | None):
    dados = dict(await request.form())
    try:
        salvo = cartoes.salvar(sessao, dados, cartao)
    except ErroValidacao as erro:
        sessao.rollback()
        return _formulario(request, sessao, cartao, dados, erro.erros, status=422)
    sessao.commit()
    return resposta_acao(f"Cartão “{salvo.nome}” salvo.")


@router.post("")
async def criar(request: Request, sessao: Sessao):
    return await _salvar(request, sessao, None)


@router.post("/{cartao_id}")
async def atualizar(request: Request, sessao: Sessao, cartao_id: int):
    return await _salvar(request, sessao, obter_ou_404(sessao, Cartao, cartao_id))


@router.post("/{cartao_id}/arquivar")
def arquivar(sessao: Sessao, cartao_id: int):
    cartao = obter_ou_404(sessao, Cartao, cartao_id)
    cartoes.definir_ativo(sessao, cartao, False)
    sessao.commit()
    return resposta_acao(f"Cartão “{cartao.nome}” arquivado.")


@router.post("/{cartao_id}/reativar")
def reativar(sessao: Sessao, cartao_id: int):
    cartao = obter_ou_404(sessao, Cartao, cartao_id)
    cartoes.definir_ativo(sessao, cartao, True)
    sessao.commit()
    return resposta_acao(f"Cartão “{cartao.nome}” reativado.")


# ───────── Pagamento da fatura (E2-19) ─────────


def _formulario_pagamento(
    request, sessao, fatura: Fatura, pagamento: Lancamento | None = None,
    valores=None, erros=None, status=200,
):  # fmt: skip
    resumo = cartoes.resumo(sessao, fatura, hoje())
    if valores is None:
        if pagamento:
            valor, data, conta_id = pagamento.valor_centavos, pagamento.data, pagamento.conta_id
        else:
            valor, data = resumo.restante, hoje()
            conta_id = fatura.cartao.conta_pagamento_id
        valores = {"valor": _texto_valor(valor) if valor else "", "data": data.isoformat(),
                   "conta_id": str(conta_id)}  # fmt: skip
    opcoes_contas = _opcoes_contas(sessao, pagamento.conta_id if pagamento else None)
    return templates.TemplateResponse(
        request,
        "partials/pagamento_form.html",
        {
            "fatura": fatura,
            "referencia": Competencia.de_texto(fatura.referencia),
            "resumo": resumo,
            "pagamento": pagamento,
            "valores": valores,
            "erros": erros or {},
            "contas": opcoes_contas,
        },
        status_code=status,
    )


@router.get("/faturas/{fatura_id}/pagar")
def pagar_form(request: Request, sessao: Sessao, fatura_id: int):
    return _formulario_pagamento(request, sessao, obter_ou_404(sessao, Fatura, fatura_id))


@router.post("/faturas/{fatura_id}/pagamentos")
async def pagar(request: Request, sessao: Sessao, fatura_id: int):
    fatura = obter_ou_404(sessao, Fatura, fatura_id)
    dados = dict(await request.form())
    try:
        pagamento = cartoes.pagar(sessao, fatura, dados)
    except ErroValidacao as erro:
        sessao.rollback()
        return _formulario_pagamento(request, sessao, fatura, None, dados, erro.erros, 422)
    sessao.commit()
    return resposta_acao(f"Pagamento de {formatar_brl(pagamento.valor_centavos)} registrado.")


@router.get("/pagamentos/{pagamento_id}/editar")
def editar_pagamento(request: Request, sessao: Sessao, pagamento_id: int):
    pagamento = obter_ou_404(sessao, Lancamento, pagamento_id)
    return _formulario_pagamento(request, sessao, pagamento.fatura, pagamento)


@router.post("/pagamentos/{pagamento_id}")
async def atualizar_pagamento(request: Request, sessao: Sessao, pagamento_id: int):
    pagamento = obter_ou_404(sessao, Lancamento, pagamento_id)
    dados = dict(await request.form())
    try:
        cartoes.pagar(sessao, pagamento.fatura, dados, pagamento)
    except ErroValidacao as erro:
        sessao.rollback()
        return _formulario_pagamento(
            request, sessao, pagamento.fatura, pagamento, dados, erro.erros, 422
        )
    sessao.commit()
    return resposta_acao("Pagamento atualizado.")

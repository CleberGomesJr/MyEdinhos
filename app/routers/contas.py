from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db.models import Conta, TipoConta
from app.db.session import obter_sessao
from app.domain import cartoes, contas
from app.domain.datas import hoje
from app.domain.dinheiro import formatar_brl
from app.domain.formularios import ErroValidacao
from app.domain.recorrencias import gerar_pendentes, limite_geracao
from app.web import mes_da_requisicao, obter_ou_404, resposta_acao, templates

router = APIRouter(prefix="/contas")
Sessao = Annotated[Session, Depends(obter_sessao)]


@router.get("")
def pagina(request: Request, sessao: Sessao):
    mes = mes_da_requisicao(request)
    dia = hoje()
    gerar_pendentes(sessao, limite_geracao(mes, dia))
    sessao.commit()
    todas = contas.listar(sessao, incluir_arquivadas=True)
    atuais = contas.saldos(sessao, dia)
    previstos = contas.saldos(sessao, mes.ultimo_dia, incluir_pendentes=True)
    for conta_id, a_pagar in cartoes.a_pagar_por_conta(sessao, mes.ultimo_dia, dia).items():
        if conta_id in previstos:
            previstos[conta_id] -= a_pagar
    ativas = [conta for conta in todas if conta.ativa]
    return templates.TemplateResponse(
        request,
        "pages/contas.html",
        {
            "ativas": ativas,
            "arquivadas": [conta for conta in todas if not conta.ativa],
            "atuais": atuais,
            "previstos": previstos,
            "total_atual": sum(atuais.get(conta.id, 0) for conta in ativas),
            "total_previsto": sum(previstos.get(conta.id, 0) for conta in ativas),
        },
    )


def _formulario(request, conta: Conta | None, valores=None, erros=None, status=200):
    if valores is None:
        valores = {
            "nome": conta.nome if conta else "",
            "tipo": conta.tipo.value if conta else TipoConta.CORRENTE.value,
            "saldo_inicial": formatar_brl(conta.saldo_inicial_centavos)
            .replace("R$ ", "")
            .replace("−", "-")
            if conta
            else "",
            "data_saldo_inicial": (conta.data_saldo_inicial if conta else hoje()).isoformat(),
            "reserva_emergencia": bool(conta and conta.reserva_emergencia),
        }
    return templates.TemplateResponse(
        request,
        "partials/conta_form.html",
        {
            "conta": conta,
            "valores": valores,
            "erros": erros or {},
            "tipos": [(tipo.value, tipo.rotulo) for tipo in TipoConta],
        },
        status_code=status,
    )


@router.get("/nova")
def nova(request: Request):
    return _formulario(request, None)


@router.get("/{conta_id}/editar")
def editar(request: Request, sessao: Sessao, conta_id: int):
    return _formulario(request, obter_ou_404(sessao, Conta, conta_id))


async def _salvar(request: Request, sessao: Session, conta: Conta | None):
    dados = await request.form()
    try:
        salva = contas.salvar(sessao, dados, conta)
    except ErroValidacao as erro:
        sessao.rollback()
        valores = {**dados, "reserva_emergencia": dados.get("reserva_emergencia") == "1"}
        return _formulario(request, conta, valores, erro.erros, status=422)
    sessao.commit()
    return resposta_acao(f"Conta “{salva.nome}” salva.")


@router.post("")
async def criar(request: Request, sessao: Sessao):
    return await _salvar(request, sessao, None)


@router.post("/{conta_id}")
async def atualizar(request: Request, sessao: Sessao, conta_id: int):
    return await _salvar(request, sessao, obter_ou_404(sessao, Conta, conta_id))


@router.post("/{conta_id}/arquivar")
def arquivar(sessao: Sessao, conta_id: int):
    conta = obter_ou_404(sessao, Conta, conta_id)
    contas.definir_ativa(sessao, conta, False)
    sessao.commit()
    return resposta_acao(f"Conta “{conta.nome}” arquivada.", fechar_drawer=True)


@router.post("/{conta_id}/reativar")
def reativar(sessao: Sessao, conta_id: int):
    conta = obter_ou_404(sessao, Conta, conta_id)
    contas.definir_ativa(sessao, conta, True)
    sessao.commit()
    return resposta_acao(f"Conta “{conta.nome}” reativada.")

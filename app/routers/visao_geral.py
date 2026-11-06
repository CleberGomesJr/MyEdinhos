"""Visão geral (E3): KPIs do mês, fluxo, categorias, saldos, a pagar e cartões."""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db.session import obter_sessao
from app.domain import assistente, contas, orcamentos, resumos
from app.domain.datas import hoje
from app.domain.recorrencias import gerar_pendentes, limite_geracao
from app.web import mes_da_requisicao, templates, visao_caixa

router = APIRouter()
Sessao = Annotated[Session, Depends(obter_sessao)]


def _serie(nome: str, cor: str, valores: list[int]) -> dict:
    return {"nome": nome, "cor": cor, "valores": valores}


@router.get("/")
def visao_geral(request: Request, sessao: Sessao):
    mes = mes_da_requisicao(request)
    dia = hoje()
    gerar_pendentes(sessao, limite_geracao(mes, dia))
    sessao.commit()

    caixa = visao_caixa(request)
    anterior = mes.anterior()
    atual = resumos.resultado(sessao, mes, caixa)
    passado = resumos.resultado(sessao, anterior, caixa)
    futuro = mes.primeiro_dia > dia
    referencia = resumos.dia_de_referencia(mes, dia)
    saldo_hoje = resumos.saldo_total(sessao, referencia)
    saldo = {
        "futuro": futuro,
        "atual": mes.primeiro_dia <= dia <= mes.ultimo_dia,
        "referencia": referencia,
        "valor": resumos.saldo_previsto(sessao, mes, dia) if futuro else saldo_hoje,
        "variacao": None
        if futuro
        else resumos.variacao(saldo_hoje, resumos.saldo_total(sessao, anterior.ultimo_dia)),
        "faturas": resumos.faturas_a_pagar(sessao, mes.ultimo_dia, dia),
    }

    fluxo = resumos.fluxo(sessao, mes, caixa=caixa)
    grafico_fluxo = {
        "rotulos": [m.rotulo_curto for m, _ in fluxo],
        "formato": "brl",
        "series": [
            _serie("Entradas", "entrada", [r.entradas for _, r in fluxo]),
            _serie("Saídas", "saida", [r.saidas for _, r in fluxo]),
        ],
    }
    diario = resumos.saldo_diario(sessao, mes, dia)
    mensal = resumos.saldo_mensal(sessao, mes, dia)

    contexto = {
        "assistente_pendente": assistente.pendente(sessao),
        "anterior": anterior,
        "resultado": atual,
        "variacao_entradas": resumos.variacao(atual.entradas, passado.entradas),
        "variacao_saidas": resumos.variacao(atual.saidas, passado.saidas),
        "saldo": saldo,
        "fluxo": grafico_fluxo,
        "tem_fluxo": any(r.entradas or r.saidas for _, r in fluxo),
        "categorias": resumos.despesas_por_categoria(sessao, mes, caixa),
        "orcamento": [ln for ln in orcamentos.listar(sessao, mes) if ln.limite][:5],
        "saldo_diario": {
            "rotulos": [d.strftime("%d/%m") for d, _ in diario],
            "formato": "brl",
            "series": [_serie("Saldo", "patrimonio", [v for _, v in diario])],
        },
        "saldo_mensal": {
            "rotulos": [m.rotulo_curto for m, _ in mensal],
            "formato": "brl",
            "series": [_serie("Saldo", "patrimonio", [v for _, v in mensal])],
        },
        "tem_saldo": bool(contas.listar(sessao)) and bool(mensal),
        "contas": resumos.saldos_por_conta(sessao, referencia),
        "ultimos": resumos.ultimos(sessao, dia),
        "a_pagar": resumos.a_pagar(sessao, dia),
        "cartoes": resumos.uso_dos_cartoes(sessao, dia),
        "hoje": dia,
        "ate_7_dias": dia + timedelta(days=7),
    }
    sessao.commit()  # a fatura aberta de cada cartão passa a existir
    return templates.TemplateResponse(request, "pages/visao_geral.html", contexto)

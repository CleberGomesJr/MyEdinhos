"""Carteira (E4): visão geral, ativos, operações, posição inicial e cotação manual."""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import agora_utc
from app.db.models import (
    Ativo,
    ClasseAtivo,
    Cotacao,
    Indexador,
    Operacao,
    ProventoAnunciado,
    TipoOperacao,
)
from app.db.session import obter_sessao
from app.domain import ativos, carteira, contas, dividendos, mercado, operacoes, proventos
from app.domain.ativos import ler_decimal
from app.domain.competencia import Competencia
from app.domain.datas import hoje
from app.domain.dinheiro import formatar_brl
from app.domain.formularios import ErroValidacao, Leitor
from app.web import obter_ou_404, resposta_acao, templates

router = APIRouter(prefix="/carteira")
Sessao = Annotated[Session, Depends(obter_sessao)]


def _serie(nome: str, cor: str, valores: list[int]) -> dict:
    return {"nome": nome, "cor": cor, "valores": valores}


def contexto_mercado(sessao: Session) -> dict:
    """Status das cotações para o cabeçalho das telas de investimento (E5-07)."""
    return {
        "mercado_status": mercado.status(sessao),
        "atualizar_agora": mercado.precisa_atualizar(sessao, agora_utc().date()),
    }


@router.get("")
def pagina(request: Request, sessao: Sessao, ordem: str = "valor"):
    dia = hoje()
    cart = carteira.montar(sessao, dia, ordem)
    mes = Competencia.de_data(dia)
    evolucao = carteira.evolucao(sessao, mes, dia)
    por_mes = proventos.por_mes(sessao, mes)
    por_unidade = proventos.proventos_por_unidade_12m(sessao, dia)
    return templates.TemplateResponse(
        request,
        "pages/carteira.html",
        {
            "cart": cart,
            "ordem": ordem if ordem in carteira.ORDENS else "valor",
            "alocacao": carteira.alocacao(cart),
            "dy": {ln.ativo.id: proventos.dividend_yield(ln, por_unidade) for ln in cart.linhas},
            "evolucao": {
                "rotulos": [m.rotulo_curto for m, _, _ in evolucao],
                "formato": "brl",
                "series": [
                    _serie("Patrimônio", "patrimonio", [p for _, p, _ in evolucao]),
                    _serie("Aportado", "outros", [a for _, _, a in evolucao]),
                ],
            },
            "proventos_mes": {
                "rotulos": [m.rotulo_curto for m, _ in por_mes],
                "formato": "brl",
                "series": [_serie("Proventos", "entrada", [v for _, v in por_mes])],
            },
            "tem_proventos": any(v for _, v in por_mes),
            "ranking": proventos.ranking_ativos(sessao, dia - timedelta(days=365), dia, limite=5),
            "arquivados": [a for a in ativos.listar(sessao, incluir_inativos=True) if not a.ativo],
            **contexto_mercado(sessao),
        },
    )


# ───────── Ativos (E4-02) ─────────


def _formulario_ativo(request, ativo: Ativo | None, valores=None, erros=None, status=200):
    if valores is None:
        valores = {
            "ticker": ativo.ticker if ativo else "",
            "nome": ativo.nome if ativo else "",
            "classe": ativo.classe.value if ativo else ClasseAtivo.ACAO.value,
            "setor": (ativo.setor or "") if ativo else "",
            "indexador": ativo.indexador.value if ativo and ativo.indexador else "",
            "taxa": str(ativo.taxa).replace(".", ",") if ativo and ativo.taxa is not None else "",
            "vencimento": ativo.vencimento.isoformat() if ativo and ativo.vencimento else "",
            "reserva_emergencia": bool(ativo and ativo.reserva_emergencia),
        }
    return templates.TemplateResponse(
        request,
        "partials/ativo_form.html",
        {
            "ativo": ativo,
            "valores": valores,
            "erros": erros or {},
            "classes": [(c.value, c.rotulo) for c in ClasseAtivo],
            "indexadores": [("", "Sem indexador")] + [(i.value, i.rotulo) for i in Indexador],
        },
        status_code=status,
    )


@router.get("/ativos/novo")
def novo_ativo(request: Request):
    return _formulario_ativo(request, None)


@router.get("/ativos/{ativo_id}/editar")
def editar_ativo(request: Request, sessao: Sessao, ativo_id: int):
    return _formulario_ativo(request, obter_ou_404(sessao, Ativo, ativo_id))


async def _salvar_ativo(request: Request, sessao: Session, ativo: Ativo | None):
    dados = dict(await request.form())
    try:
        salvo = ativos.salvar(sessao, dados, ativo)
    except ErroValidacao as erro:
        sessao.rollback()
        valores = {**dados, "reserva_emergencia": dados.get("reserva_emergencia") == "1"}
        return _formulario_ativo(request, ativo, valores, erro.erros, status=422)
    sessao.commit()
    return resposta_acao(f"{salvo.ticker} salvo.")


@router.post("/ativos")
async def criar_ativo(request: Request, sessao: Sessao):
    return await _salvar_ativo(request, sessao, None)


@router.post("/ativos/{ativo_id}")
async def atualizar_ativo(request: Request, sessao: Sessao, ativo_id: int):
    return await _salvar_ativo(request, sessao, obter_ou_404(sessao, Ativo, ativo_id))


@router.post("/ativos/{ativo_id}/arquivar")
def arquivar_ativo(sessao: Sessao, ativo_id: int):
    ativo = obter_ou_404(sessao, Ativo, ativo_id)
    ativos.definir_ativo(sessao, ativo, False)
    sessao.commit()
    return resposta_acao(f"{ativo.ticker} arquivado: sai das listas e da atualização de cotações.")


@router.post("/ativos/{ativo_id}/reativar")
def reativar_ativo(sessao: Sessao, ativo_id: int):
    ativo = obter_ou_404(sessao, Ativo, ativo_id)
    ativos.definir_ativo(sessao, ativo, True)
    sessao.commit()
    return resposta_acao(f"{ativo.ticker} reativado.")


@router.get("/ativos/{ativo_id}")
def pagina_ativo(request: Request, sessao: Sessao, ativo_id: int):
    ativo = obter_ou_404(sessao, Ativo, ativo_id)
    dia = hoje()
    ops = carteira.operacoes(sessao, ativo_id=ativo.id)
    posicao = carteira.calcular(o for o in ops if o.data <= dia).get(ativo.id)
    cotacao = carteira.ultimas_cotacoes(sessao, dia).get(ativo.id)
    linha = None
    if posicao and posicao.aberta:
        valor, origem = carteira.valor_da_posicao(sessao, posicao, cotacao, dia)
        recebido = carteira.proventos_por_ativo(sessao, dia - timedelta(days=365), dia)
        linha = carteira.LinhaCarteira(posicao, valor, origem, cotacao, recebido.get(ativo.id, 0))
    historico = list(
        sessao.scalars(
            select(Cotacao)
            .where(Cotacao.ativo_id == ativo.id, Cotacao.data > dia - timedelta(days=365))
            .order_by(Cotacao.data)
        )
    )
    anunciados = list(
        sessao.scalars(
            select(ProventoAnunciado)
            .where(ProventoAnunciado.ativo_id == ativo.id)
            .order_by(ProventoAnunciado.data_com.desc())
            .limit(12)
        )
    )
    ind = dividendos.indicadores(sessao, [ativo], dia)[ativo.id]
    return templates.TemplateResponse(
        request,
        "pages/ativo.html",
        {
            "ativo": ativo,
            "ind": ind,
            "ano_atual": dia.year,
            "dy": proventos.dividend_yield(linha, proventos.proventos_por_unidade_12m(sessao, dia))
            if linha
            else None,
            "operacoes": list(reversed(ops)),
            "posicao": posicao,
            "linha": linha,
            "cotacao": cotacao,
            "anunciados": anunciados,
            "grafico_cotacao": {
                "rotulos": [c.data.strftime("%d/%m/%y") for c in historico],
                "formato": "brl",
                "series": [
                    _serie("Fechamento", "patrimonio", [int(c.fechamento * 100) for c in historico])
                ],
            },
            **contexto_mercado(sessao),
        },
    )


# ───────── Operações (E4-03) ─────────


def _formulario_operacao(
    request, sessao, operacao: Operacao | None, valores=None, erros=None, status=200
):
    if valores is None:
        if operacao:
            valores = {
                "ativo_id": str(operacao.ativo_id),
                "tipo": operacao.tipo.value,
                "data": operacao.data.isoformat(),
                "quantidade": operacoes.formatar_quantidade(operacao.quantidade).replace(".", "")
                if operacao.quantidade is not None
                else "",
                "preco_unitario": format(operacao.preco_unitario, "f").replace(".", ",")
                if operacao.preco_unitario is not None
                else "",
                "taxas": formatar_brl(operacao.taxas_centavos).replace("R$ ", "")
                if operacao.taxas_centavos
                else "",
                "valor": formatar_brl(operacao.valor_total_centavos).replace("R$ ", ""),
                "conta_id": str(operacao.conta_id or ""),
                "observacao": operacao.observacao or "",
            }
        else:
            parametros = request.query_params
            valores = {
                "ativo_id": parametros.get("ativo_id", ""),
                "tipo": parametros.get("tipo", TipoOperacao.COMPRA.value),
                "data": parametros.get("data", hoje().isoformat()),
                "valor": parametros.get("valor", ""),
                "quantidade": parametros.get("quantidade", ""),
                "anuncio_id": parametros.get("anuncio_id", ""),
                "conta_id": "",
            }
    manter = operacao.conta_id if operacao else None
    return templates.TemplateResponse(
        request,
        "partials/operacao_form.html",
        {
            "operacao": operacao,
            "valores": valores,
            "erros": erros or {},
            "ativos": [
                (a.id, f"{a.ticker} · {a.nome}" if a.nome != a.ticker else a.ticker)
                for a in ativos.listar(sessao, incluir_inativos=bool(operacao))
            ],  # fmt: skip
            "tipos": [(t.value, t.rotulo) for t in TipoOperacao],
            "contas": [("", "Nenhuma (não mexe em conta)")]
            + [
                (c.id, c.nome)
                for c in contas.listar(sessao, incluir_arquivadas=True)
                if c.ativa or c.id == manter
            ],  # fmt: skip
        },
        status_code=status,
    )


@router.get("/operacoes/nova")
def nova_operacao(request: Request, sessao: Sessao):
    return _formulario_operacao(request, sessao, None)


@router.get("/operacoes/{operacao_id}/editar")
def editar_operacao(request: Request, sessao: Sessao, operacao_id: int):
    return _formulario_operacao(request, sessao, obter_ou_404(sessao, Operacao, operacao_id))


async def _salvar_operacao(request: Request, sessao: Session, operacao: Operacao | None):
    dados = dict(await request.form())
    try:
        salva = operacoes.salvar(sessao, dados, operacao)
    except ErroValidacao as erro:
        sessao.rollback()
        return _formulario_operacao(request, sessao, operacao, dados, erro.erros, status=422)
    # Provento lançado a partir de uma sugestão (E5-12): liga o anúncio à operação.
    anuncio_id = Leitor(dados).id("anuncio_id", obrigatorio=False)
    if anuncio_id and (anuncio := sessao.get(ProventoAnunciado, anuncio_id)):
        anuncio.operacao_id = salva.id
    sessao.commit()
    return resposta_acao(f"{salva.tipo.rotulo} de {salva.ativo.ticker} salva.")


@router.post("/operacoes")
async def criar_operacao(request: Request, sessao: Sessao):
    return await _salvar_operacao(request, sessao, None)


@router.post("/operacoes/{operacao_id}")
async def atualizar_operacao(request: Request, sessao: Sessao, operacao_id: int):
    return await _salvar_operacao(request, sessao, obter_ou_404(sessao, Operacao, operacao_id))


@router.delete("/operacoes/{operacao_id}")
def excluir_operacao(sessao: Sessao, operacao_id: int):
    registro = operacoes.excluir(sessao, obter_ou_404(sessao, Operacao, operacao_id))
    sessao.commit()
    return resposta_acao(f"{registro.descricao}: excluída.", desfazer=f"/desfazer/{registro.id}")


# ───────── Posição inicial em lote (E4-15) e cotação manual (E4-05) ─────────


def _formulario_lote(request, valores=None, erros=None, status=200):
    return templates.TemplateResponse(
        request,
        "partials/posicao_inicial_form.html",
        {"valores": valores or {"data": hoje().isoformat(), "linhas": ""}, "erros": erros or {}},
        status_code=status,
    )


@router.get("/posicao-inicial")
def posicao_inicial(request: Request):
    return _formulario_lote(request)


@router.post("/posicao-inicial")
async def salvar_posicao_inicial(request: Request, sessao: Sessao):
    dados = dict(await request.form())
    try:
        criadas = ativos.importar_posicao_inicial(sessao, dados)
    except ErroValidacao as erro:
        sessao.rollback()
        return _formulario_lote(request, dados, erro.erros, status=422)
    sessao.commit()
    return resposta_acao(f"Posição inicial de {len(criadas)} ativo(s) registrada.")


def _formulario_cotacao(request, ativo, valores=None, erros=None, status=200):
    return templates.TemplateResponse(
        request,
        "partials/cotacao_form.html",
        {
            "ativo": ativo,
            "valores": valores or {"data": hoje().isoformat(), "preco": ""},
            "erros": erros or {},
        },  # fmt: skip
        status_code=status,
    )


@router.get("/ativos/{ativo_id}/cotacao")
def cotacao_manual(request: Request, sessao: Sessao, ativo_id: int):
    return _formulario_cotacao(request, obter_ou_404(sessao, Ativo, ativo_id))


@router.post("/ativos/{ativo_id}/cotacao")
async def salvar_cotacao_manual(request: Request, sessao: Sessao, ativo_id: int):
    ativo = obter_ou_404(sessao, Ativo, ativo_id)
    dados = dict(await request.form())
    leitor = Leitor(dados)
    data = leitor.data("data")
    preco = ler_decimal(leitor, "preco")
    if leitor.erros:
        return _formulario_cotacao(request, ativo, dados, leitor.erros, status=422)
    existente = sessao.scalar(
        select(Cotacao).where(Cotacao.ativo_id == ativo.id, Cotacao.data == data)
    )
    if existente is None:
        existente = Cotacao(ativo_id=ativo.id, data=data)
        sessao.add(existente)
    existente.fechamento, existente.fonte = preco, "manual"
    sessao.commit()
    return resposta_acao(f"Cotação de {ativo.ticker} registrada.")

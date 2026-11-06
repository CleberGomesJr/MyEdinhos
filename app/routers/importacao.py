"""Importar extrato (E2-13) e operações da corretora (E4-11): arquivo → colunas → revisão.

Nada fica guardado entre os passos: o conteúdo do arquivo vai num campo oculto, em base64, e é
lido de novo a cada passo. Assim, a confirmação sempre confere o arquivo contra o banco atual.
"""

import base64
import binascii
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.db.models import Lancamento, Operacao, TipoCategoria, TipoOperacao
from app.db.session import obter_sessao
from app.domain import cartoes, categorias, contas, importacao, importacao_operacoes
from app.domain.formularios import ErroValidacao
from app.domain.planilhas import ErroArquivo, Tabela, decodificar, ler_tabela
from app.web import resposta_acao, templates

router = APIRouter()
Sessao = Annotated[Session, Depends(obter_sessao)]

LIMITE_BYTES = 5 * 1024 * 1024
PASSOS = ("Arquivo", "Colunas", "Revisão")
AMOSTRA = 5


async def _arquivo_enviado(request: Request):
    """(formulário, bytes). Levanta ErroArquivo."""
    if int(request.headers.get("content-length") or 0) > LIMITE_BYTES:
        raise ErroArquivo("Arquivo grande demais (limite de 5 MB). Exporte um período menor.")
    formulario = await request.form()
    arquivo = formulario.get("arquivo")
    if arquivo is None or isinstance(arquivo, str) or not arquivo.filename:
        raise ErroArquivo("Escolha o arquivo.")
    conteudo = await arquivo.read()
    if not conteudo.strip():
        raise ErroArquivo("O arquivo está vazio.")
    return formulario, conteudo


def _conteudo_oculto(formulario) -> bytes:
    try:
        return base64.b64decode(formulario.get("conteudo", ""), validate=True)
    except binascii.Error as erro:
        raise ErroArquivo("Não consegui reler o arquivo. Envie de novo.") from erro


def _base64(conteudo: bytes) -> str:
    return base64.b64encode(conteudo).decode("ascii")


def _opcoes_colunas(tabela: Tabela, vazio: str = "—") -> list[tuple[str, str]]:
    return [("", vazio), *((coluna, coluna) for coluna in tabela.cabecalho if coluna)]


# ───────── Extrato de conta ou cartão (E2-13) ─────────


def _origens(sessao: Session) -> list[tuple[str, str]]:
    return [
        *((f"conta-{conta.id}", f"Conta: {conta.nome}") for conta in contas.listar(sessao)),
        *((f"cartao-{c.id}", f"Cartão: {c.nome}") for c in cartoes.listar(sessao)),
    ]


def _tela_extrato_arquivo(
    request: Request, sessao: Session, erro: str | None = None, origem: str = "", status: int = 200
):
    lote = importacao.ler_lote(sessao, importacao.LOTE_EXTRATO, Lancamento)
    return templates.TemplateResponse(
        request,
        "pages/importar_extrato.html",
        {
            "etapa": "arquivo",
            "passos": PASSOS,
            "indice": 0,
            "origens": _origens(sessao),
            "origem": origem,
            "erro": erro,
            "ultimo_lote": len(lote),
        },
        status_code=status,
    )


def _tela_extrato_colunas(
    request: Request,
    origem: importacao.Origem,
    conteudo: bytes,
    tabela: Tabela,
    mapa: importacao.Mapeamento,
    erro: str | None = None,
    status: int = 200,
):
    return templates.TemplateResponse(
        request,
        "pages/importar_extrato.html",
        {
            "etapa": "colunas",
            "passos": PASSOS,
            "indice": 1,
            "origem": origem,
            "conteudo": _base64(conteudo),
            "tabela": tabela,
            "amostra": tabela.linhas[:AMOSTRA],
            "colunas": _opcoes_colunas(tabela),
            "mapa": mapa,
            "erro": erro,
        },
        status_code=status,
    )


def _mapa_do_formulario(formulario) -> importacao.Mapeamento:
    return importacao.Mapeamento(
        data=formulario.get("col_data", ""),
        descricao=formulario.get("col_descricao", ""),
        valor=formulario.get("col_valor", ""),
        credito=formulario.get("col_credito", ""),
        debito=formulario.get("col_debito", ""),
        positivo_e_saida=formulario.get("sinal") == "positivo",
    )


def _erro_do_mapa(mapa: importacao.Mapeamento) -> str | None:
    if not mapa.data or not mapa.descricao:
        return "Indique as colunas de data e de descrição."
    if not mapa.valor and not (mapa.credito or mapa.debito):
        return "Indique a coluna de valor, ou as de crédito e débito."
    return None


def _tela_extrato_revisao(
    request: Request,
    sessao: Session,
    origem: importacao.Origem,
    conteudo: bytes,
    mapa: importacao.Mapeamento | None,
    erro: str | None = None,
    status: int = 200,
):
    transacoes, erros = importacao.ler_arquivo(conteudo, origem, mapa)
    itens = importacao.revisar(sessao, transacoes, origem)
    return templates.TemplateResponse(
        request,
        "pages/importar_extrato.html",
        {
            "etapa": "revisao",
            "passos": PASSOS,
            "indice": 2,
            "origem": origem,
            "conteudo": _base64(conteudo),
            "mapa": mapa,
            "itens": itens,
            "erros_arquivo": erros,
            "categorias_saida": categorias.opcoes(sessao, TipoCategoria.SAIDA),
            "categorias_entrada": categorias.opcoes(sessao, TipoCategoria.ENTRADA),
            "marcadas": sum(item.importar for item in itens),
            "antes_do_saldo": bool(
                origem.conta and any(t.data < origem.conta.data_saldo_inicial for t in transacoes)
            ),
            "erro": erro,
        },
        status_code=status,
    )


@router.get("/lancamentos/importar")
def extrato_pagina(request: Request, sessao: Sessao, origem: str = ""):
    return _tela_extrato_arquivo(request, sessao, origem=origem)


@router.post("/lancamentos/importar")
async def extrato_ler(request: Request, sessao: Sessao):
    texto_origem = ""
    try:
        formulario, conteudo = await _arquivo_enviado(request)
        texto_origem = formulario.get("origem", "")
        origem = importacao.ler_origem(sessao, texto_origem)
        if origem is None:
            raise ErroArquivo("Escolha a conta ou o cartão do extrato.")
        texto = decodificar(conteudo)
        if importacao.eh_ofx(texto):
            return _tela_extrato_revisao(request, sessao, origem, conteudo, None)
        tabela = ler_tabela(texto)
    except ErroArquivo as erro:
        return _tela_extrato_arquivo(request, sessao, str(erro), texto_origem, status=422)
    mapa = importacao.adivinhar_mapeamento(tabela, origem)
    return _tela_extrato_colunas(request, origem, conteudo, tabela, mapa)


async def _passo_seguinte(request: Request, sessao: Session):
    """(formulário, origem, conteúdo, mapa) dos passos depois do envio. Levanta ErroArquivo."""
    formulario = await request.form()
    origem = importacao.ler_origem(sessao, formulario.get("origem", ""))
    if origem is None:
        raise ErroArquivo("Conta ou cartão não encontrado. Comece de novo.")
    conteudo = _conteudo_oculto(formulario)
    mapa = None if importacao.eh_ofx(decodificar(conteudo)) else _mapa_do_formulario(formulario)
    return formulario, origem, conteudo, mapa


@router.post("/lancamentos/importar/revisar")
async def extrato_revisar(request: Request, sessao: Sessao):
    try:
        _, origem, conteudo, mapa = await _passo_seguinte(request, sessao)
        if mapa is not None and (erro := _erro_do_mapa(mapa)):
            tabela = ler_tabela(decodificar(conteudo))
            return _tela_extrato_colunas(request, origem, conteudo, tabela, mapa, erro, 422)
        return _tela_extrato_revisao(request, sessao, origem, conteudo, mapa)
    except ErroArquivo as erro:
        return _tela_extrato_arquivo(request, sessao, str(erro), status=422)


@router.post("/lancamentos/importar/confirmar")
async def extrato_confirmar(request: Request, sessao: Sessao):
    try:
        formulario, origem, conteudo, mapa = await _passo_seguinte(request, sessao)
        transacoes, _ = importacao.ler_arquivo(conteudo, origem, mapa)
    except ErroArquivo as erro:
        return _tela_extrato_arquivo(request, sessao, str(erro), status=422)
    try:
        criados = importacao.confirmar(sessao, transacoes, origem, formulario)
    except ErroValidacao as erro:
        sessao.rollback()
        mensagem = next(iter(erro.erros.values()))
        return _tela_extrato_revisao(
            request, sessao, origem, conteudo, mapa, f"Nada foi gravado: {mensagem}", 422
        )
    importacao.guardar_lote(sessao, importacao.LOTE_EXTRATO, criados)
    sessao.commit()
    return templates.TemplateResponse(
        request,
        "pages/importar_extrato.html",
        {
            "etapa": "fim",
            "passos": PASSOS,
            "indice": 3,
            "origem": origem,
            "criados": len(criados),
            "mes_importado": max((c.competencia for c in criados), default=None),
        },
    )


@router.post("/lancamentos/importar/desfazer")
def extrato_desfazer(sessao: Sessao):
    registro = importacao.desfazer_lote(
        sessao, importacao.LOTE_EXTRATO, Lancamento, "Importação de {} lançamento(s)"
    )
    sessao.commit()
    if registro is None:
        return resposta_acao("Não há importação para desfazer.", tipo="info", fechar_drawer=False)
    return resposta_acao(
        f"{registro.descricao} desfeita.", desfazer=f"/desfazer/{registro.id}", fechar_drawer=False
    )


# ───────── Operações da corretora (E4-11) ─────────


def _tela_operacoes_arquivo(
    request: Request, sessao: Session, erro: str | None = None, status: int = 200
):
    lote = importacao.ler_lote(sessao, importacao_operacoes.LOTE, Operacao)
    return templates.TemplateResponse(
        request,
        "pages/importar_operacoes.html",
        {
            "etapa": "arquivo",
            "passos": PASSOS,
            "indice": 0,
            "erro": erro,
            "ultimo_lote": len(lote),
        },
        status_code=status,
    )


def _tela_operacoes_colunas(
    request: Request,
    sessao: Session,
    conteudo: bytes,
    tabela: Tabela,
    mapa: importacao_operacoes.Mapeamento,
    conta_id: str = "",
    erro: str | None = None,
    status: int = 200,
):
    return templates.TemplateResponse(
        request,
        "pages/importar_operacoes.html",
        {
            "etapa": "colunas",
            "passos": PASSOS,
            "indice": 1,
            "conteudo": _base64(conteudo),
            "tabela": tabela,
            "amostra": tabela.linhas[:AMOSTRA],
            "colunas": _opcoes_colunas(tabela),
            "mapa": asdict(mapa),
            "contas": [("", "Nenhuma: não mexer em saldo")]
            + [(str(c.id), c.nome) for c in contas.listar(sessao)],
            "conta_id": conta_id,
            "erro": erro,
        },
        status_code=status,
    )


def _operacoes_do_formulario(formulario) -> tuple[Tabela, importacao_operacoes.Mapeamento]:
    tabela = ler_tabela(decodificar(_conteudo_oculto(formulario)))
    mapa = importacao_operacoes.Mapeamento(
        **{campo: formulario.get(f"col_{campo}", "") for campo in importacao_operacoes.COLUNAS}
    )
    return tabela, mapa


@router.get("/carteira/importar")
def operacoes_pagina(request: Request, sessao: Sessao):
    return _tela_operacoes_arquivo(request, sessao)


@router.post("/carteira/importar")
async def operacoes_ler(request: Request, sessao: Sessao):
    try:
        _, conteudo = await _arquivo_enviado(request)
        tabela = ler_tabela(decodificar(conteudo))
    except ErroArquivo as erro:
        return _tela_operacoes_arquivo(request, sessao, str(erro), 422)
    mapa = importacao_operacoes.adivinhar_mapeamento(tabela)
    return _tela_operacoes_colunas(request, sessao, conteudo, tabela, mapa)


@router.post("/carteira/importar/revisar")
async def operacoes_revisar(request: Request, sessao: Sessao):
    formulario = await request.form()
    conta_id = formulario.get("conta_id", "")
    try:
        conteudo = _conteudo_oculto(formulario)
        tabela, mapa = _operacoes_do_formulario(formulario)
    except ErroArquivo as erro:
        return _tela_operacoes_arquivo(request, sessao, str(erro), 422)
    if not (mapa.data and mapa.ticker and mapa.tipo):
        erro = "Indique as colunas de data, ativo e tipo de operação."
        return _tela_operacoes_colunas(request, sessao, conteudo, tabela, mapa, conta_id, erro, 422)
    linhas = importacao_operacoes.ler_linhas(tabela, mapa)
    importacao_operacoes.revisar(sessao, linhas, conta_id)
    return templates.TemplateResponse(
        request,
        "pages/importar_operacoes.html",
        {
            "etapa": "revisao",
            "passos": PASSOS,
            "indice": 2,
            "conteudo": _base64(conteudo),
            "mapa": asdict(mapa),
            "conta_id": conta_id,
            "linhas": linhas,
            "marcadas": sum(ln.situacao == "nova" for ln in linhas),
            "tipo_operacao": TipoOperacao,
        },
    )


@router.post("/carteira/importar/confirmar")
async def operacoes_confirmar(request: Request, sessao: Sessao):
    formulario = await request.form()
    conta_id = formulario.get("conta_id", "")
    try:
        tabela, mapa = _operacoes_do_formulario(formulario)
    except ErroArquivo as erro:
        return _tela_operacoes_arquivo(request, sessao, str(erro), 422)
    linhas = importacao_operacoes.ler_linhas(tabela, mapa)
    criadas, falhas = importacao_operacoes.confirmar(sessao, linhas, formulario, conta_id)
    importacao.guardar_lote(sessao, importacao_operacoes.LOTE, criadas)
    sessao.commit()
    return templates.TemplateResponse(
        request,
        "pages/importar_operacoes.html",
        {
            "etapa": "fim",
            "passos": PASSOS,
            "indice": 3,
            "criadas": len(criadas),
            "falhas": falhas,
        },
    )


@router.post("/carteira/importar/desfazer")
def operacoes_desfazer(sessao: Sessao) -> Response:
    registro = importacao.desfazer_lote(
        sessao, importacao_operacoes.LOTE, Operacao, "Importação de {} operação(ões)"
    )
    sessao.commit()
    if registro is None:
        return resposta_acao("Não há importação para desfazer.", tipo="info", fechar_drawer=False)
    return resposta_acao(
        f"{registro.descricao} desfeita.", desfazer=f"/desfazer/{registro.id}", fechar_drawer=False
    )

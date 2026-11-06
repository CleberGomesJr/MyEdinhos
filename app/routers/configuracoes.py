"""Configurações: dados do app, backups, exportar e importar tudo (E7-04)."""

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.backup import PADRAO_NOME, fazer_backup
from app.db.session import obter_sessao
from app.domain import portabilidade
from app.web import resposta_acao, templates

router = APIRouter(prefix="/config")
Sessao = Annotated[Session, Depends(obter_sessao)]


def _megabytes(tamanho: int) -> str:
    return f"{tamanho / 1_048_576:.1f}".replace(".", ",") + " MB"


def _backups(pasta) -> list[dict]:
    arquivos = sorted(pasta.glob("*.db"), key=lambda a: a.stat().st_mtime, reverse=True)
    return [
        {
            "nome": arquivo.name,
            "tamanho": _megabytes(arquivo.stat().st_size),
            "quando": datetime.fromtimestamp(arquivo.stat().st_mtime),
            "automatico": bool(PADRAO_NOME.match(arquivo.name)),
        }
        for arquivo in arquivos
    ]


@router.get("")
def pagina(request: Request, sessao: Sessao):
    settings = request.app.state.settings
    return templates.TemplateResponse(
        request,
        "pages/configuracoes.html",
        {
            "pasta_dados": settings.data_dir,
            "tamanho_banco": _megabytes(settings.db_path.stat().st_size),
            "versao_banco": sessao.scalar(text("SELECT version_num FROM alembic_version")),
            "pasta_backups": settings.backups_dir,
            "pasta_logs": settings.logs_dir,
            "backups": _backups(settings.backups_dir),
            "tem_token": bool(settings.brapi_token),
            "importado": request.query_params.get("importado"),
        },
    )


@router.post("/backup")
def fazer_backup_agora(request: Request):
    settings = request.app.state.settings
    destino = fazer_backup(settings.db_path, settings.backups_dir)
    return resposta_acao(f"Backup criado: {destino.name}.", fechar_drawer=False)


@router.get("/exportar.zip")
def exportar(request: Request):
    conteudo = portabilidade.exportar(request.app.state.settings)
    nome = f"myedinhos-{date.today().isoformat()}.zip"
    return Response(
        conteudo,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


def _erro_importacao(request: Request, mensagem: str):
    return templates.TemplateResponse(
        request, "partials/importar_erro.html", {"mensagem": mensagem}, status_code=422
    )


@router.post("/importar")
async def importar(request: Request):
    tamanho = int(request.headers.get("content-length") or 0)
    if tamanho > portabilidade.LIMITE_BYTES:
        return _erro_importacao(request, "Arquivo grande demais para importar.")
    arquivo = (await request.form()).get("arquivo")
    if arquivo is None or isinstance(arquivo, str) or not arquivo.filename:
        return _erro_importacao(request, "Escolha o arquivo .zip ou .db.")
    conteudo = await arquivo.read()
    try:
        portabilidade.importar(conteudo, request.app.state.settings, request.app.state.engine)
    except portabilidade.ErroImportacao as erro:
        return _erro_importacao(request, str(erro))
    return Response(status_code=204, headers={"HX-Redirect": "/config?importado=1"})

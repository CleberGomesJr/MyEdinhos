"""Exportar e importar tudo (E7-04): `.zip` com o banco e CSVs para abrir em planilha.

Importar troca o banco inteiro, então antes de tudo o arquivo é validado e o banco atual
ganha uma cópia `antes-de-importar_*.db`, que a limpeza de backups não apaga.
"""

import csv
import io
import json
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

from alembic.script import ScriptDirectory
from sqlalchemy.engine import Engine

from app.config import Settings
from app.db.backup import fazer_backup
from app.db.migracoes import aplicar_migracoes, config_alembic
from app.domain.exportacao import protegido_para_planilha

NOME_DB = "financas.db"
LIMITE_BYTES = 500 * 1024 * 1024
ASSINATURA_SQLITE = b"SQLite format 3\x00"
TABELAS_ESSENCIAIS = {"alembic_version", "contas", "categorias", "lancamentos"}
SEM_CSV = {"alembic_version", "desfazer", "preferencias", "sqlite_sequence"}

LEIAME = """MyEdinhos: exportação completa

financas.db   o banco inteiro. Para restaurar: Configurações → Importar.
csv/          uma planilha por tabela (separador ";", UTF-8).

Nos CSVs os valores em dinheiro estão em centavos (colunas *_centavos): 123456 = R$ 1.234,56.
Este arquivo tem seus dados financeiros: guarde em lugar seguro.
"""


class ErroImportacao(Exception):
    """Arquivo recusado; a mensagem é mostrada ao usuário."""


def _tabelas(conexao: sqlite3.Connection) -> list[str]:
    linhas = conexao.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )
    return sorted(nome for (nome,) in linhas)


def _csv_da_tabela(conexao: sqlite3.Connection, tabela: str) -> tuple[str, int]:
    cursor = conexao.execute(f'SELECT * FROM "{tabela}"')  # nome vem do sqlite_master
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";", lineterminator="\r\n")
    escritor.writerow(coluna[0] for coluna in cursor.description)
    linhas = 0
    for linha in cursor:
        escritor.writerow(protegido_para_planilha(v) if isinstance(v, str) else v for v in linha)
        linhas += 1
    return "﻿" + saida.getvalue(), linhas


def exportar(settings: Settings, agora: datetime | None = None) -> bytes:
    """Monta o `.zip` a partir de uma cópia consistente do banco (API de backup do SQLite)."""
    with tempfile.TemporaryDirectory() as pasta:
        copia = Path(pasta) / NOME_DB
        with (
            closing(sqlite3.connect(settings.db_path)) as origem,
            closing(sqlite3.connect(copia)) as destino,
        ):
            origem.backup(destino)
        with closing(sqlite3.connect(copia)) as conexao:
            versao = conexao.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            csvs = {t: _csv_da_tabela(conexao, t) for t in _tabelas(conexao) if t not in SEM_CSV}
        saida = io.BytesIO()
        with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as arquivo:
            arquivo.write(copia, NOME_DB)
            arquivo.writestr("LEIAME.txt", LEIAME)
            arquivo.writestr(
                "manifesto.json",
                json.dumps(
                    {
                        "app": "MyEdinhos",
                        "versao_banco": versao,
                        "criado_em": (agora or datetime.now()).isoformat(timespec="seconds"),
                        "linhas": {tabela: linhas for tabela, (_, linhas) in csvs.items()},
                    },
                    indent=2,
                ),
            )
            for tabela, (texto, _) in csvs.items():
                arquivo.writestr(f"csv/{tabela}.csv", texto)
        return saida.getvalue()


def _bytes_do_banco(conteudo: bytes) -> bytes:
    """Aceita o `.zip` exportado ou um `.db` (ex.: de `data/backups`). Só lê o banco do zip:
    nada é extraído para o disco com o nome que vem de dentro do arquivo."""
    if conteudo.startswith(ASSINATURA_SQLITE):
        return conteudo
    try:
        with zipfile.ZipFile(io.BytesIO(conteudo)) as arquivo:
            try:
                info = arquivo.getinfo(NOME_DB)
            except KeyError as erro:
                raise ErroImportacao("O .zip não tem o banco (financas.db).") from erro
            if info.file_size > LIMITE_BYTES:
                raise ErroImportacao("Banco grande demais para importar.")
            return arquivo.read(info)
    except zipfile.BadZipFile as erro:
        raise ErroImportacao("O arquivo não é um .zip do MyEdinhos nem um banco .db.") from erro


def validar_banco(caminho: Path, settings: Settings) -> None:
    uri = f"file:{caminho.as_posix()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as conexao:
            if conexao.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ErroImportacao("O banco do arquivo está corrompido.")
            faltando = TABELAS_ESSENCIAIS - set(_tabelas(conexao))
            if faltando:
                raise ErroImportacao("O arquivo não é um banco do MyEdinhos.")
            # O app não cria gatilhos nem visões: um banco com eles não veio daqui.
            extras = conexao.execute(
                "SELECT count(*) FROM sqlite_master WHERE type IN ('trigger', 'view')"
            ).fetchone()[0]
            if extras:
                raise ErroImportacao("O banco tem objetos que o MyEdinhos não usa; não importei.")
            versao = conexao.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlite3.DatabaseError as erro:
        raise ErroImportacao("O arquivo não é um banco SQLite válido.") from erro
    revisoes = ScriptDirectory.from_config(config_alembic(settings.database_url))
    if not versao or versao[0] not in {r.revision for r in revisoes.walk_revisions()}:
        raise ErroImportacao("O banco é de uma versão mais nova do app. Atualize o app antes.")


def _copiar_banco(origem: Path, destino: Path) -> None:
    with closing(sqlite3.connect(origem)) as de, closing(sqlite3.connect(destino)) as para:
        de.backup(para)


def importar(
    conteudo: bytes, settings: Settings, engine: Engine, agora: datetime | None = None
) -> Path:
    """Valida, guarda o banco atual e o substitui. Se a migração falhar, volta ao que era.
    Devolve o caminho da cópia de segurança."""
    if len(conteudo) > LIMITE_BYTES:
        raise ErroImportacao("Arquivo grande demais para importar.")
    with tempfile.TemporaryDirectory() as pasta:
        novo = Path(pasta) / NOME_DB
        novo.write_bytes(_bytes_do_banco(conteudo))
        validar_banco(novo, settings)

        fazer_backup(settings.db_path, settings.backups_dir)
        carimbo = f"{agora or datetime.now():%Y-%m-%d_%H%M%S}"
        seguranca = settings.backups_dir / f"antes-de-importar_{carimbo}.db"
        _copiar_banco(settings.db_path, seguranca)

        engine.dispose()  # ninguém segurando o arquivo durante a troca
        _copiar_banco(novo, settings.db_path)
        try:
            aplicar_migracoes(settings.database_url)
        except Exception:
            _copiar_banco(seguranca, settings.db_path)
            raise
    return seguranca

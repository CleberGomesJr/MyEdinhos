import sqlite3
from datetime import datetime, timedelta

from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.db.backup import fazer_backup
from app.db.migracoes import aplicar_migracoes, config_alembic
from app.db.session import criar_engine


def test_pragmas_da_conexao(settings):
    settings.garantir_pastas()
    engine = criar_engine(settings.database_url)
    with engine.connect() as conexao:
        assert conexao.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conexao.execute(text("PRAGMA journal_mode")).scalar() == "wal"
    engine.dispose()


def test_migracoes_chegam_ao_head(settings):
    settings.garantir_pastas()
    aplicar_migracoes(settings.database_url)
    aplicar_migracoes(settings.database_url)  # idempotente
    head = ScriptDirectory.from_config(config_alembic(settings.database_url)).get_current_head()
    with sqlite3.connect(settings.db_path) as conexao:
        versao = conexao.execute("SELECT version_num FROM alembic_version").fetchone()
        ddls = dict(
            conexao.execute(
                "SELECT name, sql FROM sqlite_master WHERE name IN ('lancamentos', 'operacoes')"
            )
        )
    assert versao == (head,)
    # O "Desfazer" reinsere linhas com o id original: o id não pode ser reaproveitado.
    assert "AUTOINCREMENT" in ddls["lancamentos"] and "AUTOINCREMENT" in ddls["operacoes"]


def test_backup_sem_banco_nao_faz_nada(tmp_path):
    assert fazer_backup(tmp_path / "nao_existe.db", tmp_path / "backups") is None


def test_backup_copia_o_conteudo(tmp_path):
    origem = tmp_path / "financas.db"
    with sqlite3.connect(origem) as conexao:
        conexao.execute("CREATE TABLE t (v INTEGER)")
        conexao.execute("INSERT INTO t VALUES (42)")

    destino = fazer_backup(origem, tmp_path / "backups", agora=datetime(2026, 10, 26, 9, 5))

    assert destino.name == "2026-10-26_0905.db"
    with sqlite3.connect(destino) as conexao:
        assert conexao.execute("SELECT v FROM t").fetchone() == (42,)


def test_backup_mantem_so_os_mais_recentes(tmp_path):
    origem = tmp_path / "financas.db"
    sqlite3.connect(origem).close()
    pasta = tmp_path / "backups"
    pasta.mkdir()
    manual = pasta / "antes-de-mexer.db"
    manual.touch()

    inicio = datetime(2026, 1, 1, 8, 0)
    for dia in range(20):
        fazer_backup(origem, pasta, manter=14, agora=inicio + timedelta(days=dia))

    automaticos = sorted(p.name for p in pasta.iterdir() if p != manual)
    assert len(automaticos) == 14
    assert automaticos[0] == "2026-01-07_0800.db"
    assert automaticos[-1] == "2026-01-20_0800.db"
    assert manual.exists()

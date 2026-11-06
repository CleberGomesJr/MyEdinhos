"""Exportar e importar tudo (E7-04)."""

import csv
import io
import json
import sqlite3
import zipfile
from contextlib import closing

import pytest
from sqlalchemy import select

from app.db.models import Conta
from app.domain import contas, portabilidade

HTMX = {"HX-Request": "true"}


def _renomear_conta(sessao, nome: str, saldo: str = "1.234,56") -> None:
    conta = sessao.scalar(select(Conta))
    contas.salvar(
        sessao,
        {
            "nome": nome,
            "tipo": "corrente",
            "saldo_inicial": saldo,
            "data_saldo_inicial": "2026-01-01",
        },
        conta,
    )
    sessao.commit()


def _nome_da_conta(settings) -> str:
    with closing(sqlite3.connect(settings.db_path)) as conexao:
        return conexao.execute("SELECT nome FROM contas ORDER BY id").fetchone()[0]


def test_zip_tem_banco_csvs_e_manifesto(sessao, settings):
    _renomear_conta(sessao, "=Conta com fórmula")
    conteudo = portabilidade.exportar(settings)
    with zipfile.ZipFile(io.BytesIO(conteudo)) as arquivo:
        nomes = arquivo.namelist()
        assert {"financas.db", "LEIAME.txt", "manifesto.json", "csv/contas.csv"} <= set(nomes)
        assert "csv/preferencias.csv" not in nomes and "csv/desfazer.csv" not in nomes
        manifesto = json.loads(arquivo.read("manifesto.json"))
        linhas = list(
            csv.reader(
                io.StringIO(arquivo.read("csv/contas.csv").decode("utf-8-sig")), delimiter=";"
            )
        )
    assert manifesto["app"] == "MyEdinhos" and manifesto["linhas"]["contas"] == 1
    assert "'=Conta com fórmula" in linhas[1]  # sem injeção de fórmula na planilha
    assert "123456" in linhas[1]


def test_exportar_e_importar_restaura_os_dados(app, sessao, settings):
    _renomear_conta(sessao, "Original")
    zipado = portabilidade.exportar(settings)
    _renomear_conta(sessao, "Mudou depois")
    sessao.close()

    seguranca = portabilidade.importar(zipado, settings, app.state.engine)
    assert _nome_da_conta(settings) == "Original"
    # O banco de antes fica guardado e fora da limpeza automática.
    assert seguranca.name.startswith("antes-de-importar_") and seguranca.exists()
    with closing(sqlite3.connect(seguranca)) as conexao:
        assert conexao.execute("SELECT nome FROM contas").fetchone()[0] == "Mudou depois"


def test_importa_db_de_backup(app, sessao, settings):
    _renomear_conta(sessao, "Do backup")
    sessao.close()
    bruto_zip = portabilidade.exportar(settings)
    with zipfile.ZipFile(io.BytesIO(bruto_zip)) as arquivo:
        banco = arquivo.read("financas.db")
    portabilidade.importar(banco, settings, app.state.engine)
    assert _nome_da_conta(settings) == "Do backup"


def _zip_com(banco: bytes, nome: str = "financas.db") -> bytes:
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w") as arquivo:
        arquivo.writestr(nome, banco)
    return saida.getvalue()


def _banco_alterado(tmp_path, sql: str) -> bytes:
    caminho = tmp_path / "x.db"
    with closing(sqlite3.connect(caminho)) as conexao:
        conexao.executescript(sql)
        conexao.commit()
    return caminho.read_bytes()


ESSENCIAIS = (
    "CREATE TABLE alembic_version (version_num TEXT); CREATE TABLE contas (id INT);"
    "CREATE TABLE categorias (id INT); CREATE TABLE lancamentos (id INT);"
)


@pytest.mark.parametrize(
    ("sql", "mensagem"),
    [
        ("CREATE TABLE qualquer (id INT);", "não é um banco do MyEdinhos"),
        (ESSENCIAIS + "INSERT INTO alembic_version VALUES ('9999');", "versão mais nova"),
        (
            ESSENCIAIS + "INSERT INTO alembic_version VALUES ('0010');"
            "CREATE TRIGGER t AFTER INSERT ON contas BEGIN SELECT 1; END;",
            "objetos que o MyEdinhos não usa",
        ),
    ],
)
def test_recusa_banco_estranho_sem_mexer_no_atual(app, sessao, settings, tmp_path, sql, mensagem):
    _renomear_conta(sessao, "Intacta")
    sessao.close()
    with pytest.raises(portabilidade.ErroImportacao, match=mensagem):
        portabilidade.importar(_banco_alterado(tmp_path, sql), settings, app.state.engine)
    assert _nome_da_conta(settings) == "Intacta"
    assert not list(settings.backups_dir.glob("antes-de-importar_*"))


def test_recusa_arquivos_que_nao_servem(app, settings):
    with pytest.raises(portabilidade.ErroImportacao, match="não é um .zip"):
        portabilidade.importar(b"texto qualquer", settings, app.state.engine)
    with pytest.raises(portabilidade.ErroImportacao, match="não tem o banco"):
        portabilidade.importar(_zip_com(b"oi", "outro.txt"), settings, app.state.engine)
    with pytest.raises(portabilidade.ErroImportacao, match="não é um banco SQLite"):
        portabilidade.importar(_zip_com(b"nao e banco"), settings, app.state.engine)


def test_zip_com_caminho_malicioso_nao_escreve_fora(app, settings, tmp_path):
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w") as arquivo:
        arquivo.writestr("../../fora.txt", "x")
    with pytest.raises(portabilidade.ErroImportacao):
        portabilidade.importar(saida.getvalue(), settings, app.state.engine)
    assert not (settings.data_dir.parent.parent / "fora.txt").exists()


# ───────── Tela ─────────


def test_tela_configuracoes(cliente, sessao, settings):
    tela = cliente.get("/config").text
    assert "Exportar tudo" in tela and "Nenhum backup ainda" in tela and "Em construção" not in tela

    backup = cliente.post("/config/backup", headers=HTMX)
    assert backup.status_code == 204
    assert ".db" in cliente.get("/config").text

    baixado = cliente.get("/config/exportar.zip")
    assert baixado.headers["content-type"] == "application/zip"
    assert 'filename="myedinhos-' in baixado.headers["content-disposition"]

    sem_arquivo = cliente.post("/config/importar", headers=HTMX, files={})
    assert sem_arquivo.status_code == 422
    ruim = cliente.post("/config/importar", headers=HTMX, files={"arquivo": ("x.zip", b"lixo")})
    assert ruim.status_code == 422 and "Nada foi alterado" in ruim.text

    certo = cliente.post(
        "/config/importar", headers=HTMX, files={"arquivo": ("m.zip", baixado.content)}
    )
    assert certo.status_code == 204 and certo.headers["hx-redirect"] == "/config?importado=1"
    assert "Dados importados" in cliente.get("/config?importado=1").text
    assert "guardado" in cliente.get("/config").text
    assert cliente.get("/contas").status_code == 200  # o app segue funcionando depois da troca

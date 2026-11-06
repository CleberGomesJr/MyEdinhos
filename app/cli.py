"""Comandos do projeto, expostos no pyproject.toml como `uv run <comando>`."""

import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

from app.config import APP_DIR, HOST, RAIZ, Settings, get_settings
from app.db.backup import fazer_backup
from app.db.migracoes import aplicar_migracoes
from app.demo import gerar as gerar_demo
from app.demo import settings_demo


def _preparar_banco() -> Settings:
    """Backup antes de tudo e depois as migrações pendentes."""
    settings = get_settings()
    settings.garantir_pastas()
    if destino := fazer_backup(settings.db_path, settings.backups_dir):
        print(f"Backup: {destino}")
    aplicar_migracoes(settings.database_url)
    return settings


def seed_demo() -> None:
    """Gera 12 meses fictícios numa pasta separada. Não toca no banco real."""
    demo = gerar_demo(get_settings())
    print(f"Demonstração pronta em {demo.data_dir}. Para abrir: uv run demo")


def demo() -> None:
    """Abre o app com os dados fictícios, em outra porta. Gera os dados se ainda não existem."""
    settings = settings_demo(get_settings())
    if not settings.db_path.exists():
        settings = gerar_demo(get_settings())
    aplicar_migracoes(settings.database_url)
    os.environ |= {
        "MYEDINHOS_DATA_DIR": str(settings.data_dir),
        "MYEDINHOS_PORTA": str(settings.porta),
        "MYEDINHOS_MODO_DEMO": "true",
        "BRAPI_TOKEN": "",
        "MYEDINHOS_BRAPI_TOKEN": "",
    }
    get_settings.cache_clear()
    url = f"http://{HOST}:{settings.porta}"
    threading.Thread(target=_abrir_quando_pronto, args=(url,), daemon=True).start()
    print(f"Demonstração em {url} (dados fictícios) — feche esta janela para encerrar.")
    _servir(settings, recarregar=False)


def _servidor_no_ar(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/saude", timeout=1) as resposta:
            return resposta.status == 200
    except OSError:
        return False


def _abrir_quando_pronto(url: str, limite_s: float = 30) -> None:
    fim = time.monotonic() + limite_s
    while time.monotonic() < fim:
        if _servidor_no_ar(url):
            webbrowser.open(url)
            return
        time.sleep(0.3)


def _servir(settings: Settings, recarregar: bool) -> None:
    uvicorn.run(
        "app.main:criar_app",
        factory=True,
        host=HOST,
        port=settings.porta,
        reload=recarregar,
        reload_dirs=[str(APP_DIR)] if recarregar else None,
    )


def dev() -> None:
    """Servidor de desenvolvimento, recarrega ao salvar código."""
    _servir(_preparar_banco(), recarregar=True)


def iniciar() -> None:
    """Uso diário: sobe o servidor e abre o navegador (ou só abre, se já estiver no ar)."""
    settings = get_settings()
    url = f"http://{HOST}:{settings.porta}"
    if _servidor_no_ar(url):
        webbrowser.open(url)
        return
    settings = _preparar_banco()
    threading.Thread(target=_abrir_quando_pronto, args=(url,), daemon=True).start()
    print(f"MyEdinhos em {url} — feche esta janela para encerrar.")
    _servir(settings, recarregar=False)


def migrate() -> None:
    """Faz backup e aplica as migrações pendentes."""
    settings = _preparar_banco()
    print(f"Banco atualizado: {settings.db_path}")


def criar_atalho(pasta: Path | None = None) -> Path:
    """Cria o atalho "MyEdinhos" para o iniciar.bat (na Área de Trabalho, se não disser onde)."""
    script = (
        "$pasta = if ($env:ATALHO_PASTA) { $env:ATALHO_PASTA } "
        "else { [Environment]::GetFolderPath('Desktop') }\n"
        "$caminho = Join-Path $pasta 'MyEdinhos.lnk'\n"
        "$atalho = (New-Object -ComObject WScript.Shell).CreateShortcut($caminho)\n"
        "$atalho.TargetPath = $env:ATALHO_ALVO\n"
        "$atalho.WorkingDirectory = $env:ATALHO_RAIZ\n"
        "$atalho.IconLocation = $env:ATALHO_ICONE\n"
        "$atalho.Description = 'MyEdinhos: controle financeiro local'\n"
        "$atalho.Save()\n"
        "Write-Output $caminho\n"
    )
    ambiente = os.environ | {
        "ATALHO_ALVO": str(RAIZ / "iniciar.bat"),
        "ATALHO_RAIZ": str(RAIZ),
        "ATALHO_ICONE": str(APP_DIR / "static" / "favicon.ico"),
        "ATALHO_PASTA": str(pasta or ""),
    }
    saida = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        env=ambiente, capture_output=True, text=True, check=True,
    )  # fmt: skip
    return Path(saida.stdout.strip())


def atalho() -> None:
    """Atalho na Área de Trabalho (Windows)."""
    if os.name != "nt":
        sys.exit("O atalho automático é só para Windows. Use: uv run iniciar")
    print(f"Atalho criado: {criar_atalho()}")


def e2e() -> None:
    """Testes de ponta a ponta no Edge instalado, com regressão visual do tema escuro."""
    sys.exit(
        subprocess.run(
            [sys.executable, "-m", "pytest", "-m", "e2e", "tests/e2e"], cwd=RAIZ
        ).returncode
    )


def check() -> None:
    """ruff (lint + formatação) e pytest. Sai com erro se algum falhar."""
    etapas = [
        ["ruff", "check", "."],
        ["ruff", "format", "--check", "."],
        ["pytest"],
    ]
    falhas = 0
    for etapa in etapas:
        print(f"\n$ {' '.join(etapa)}", flush=True)
        falhas += subprocess.run([sys.executable, "-m", *etapa], cwd=RAIZ).returncode != 0
    sys.exit(1 if falhas else 0)

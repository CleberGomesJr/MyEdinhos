@echo off
rem Sobe o MyEdinhos e abre o navegador. Feche esta janela para encerrar.
cd /d "%~dp0"
chcp 65001 >nul

where uv >nul 2>nul
if %errorlevel%==0 (
    uv run iniciar
) else (
    python -m uv run iniciar
)

if errorlevel 1 (
    echo.
    echo O MyEdinhos encerrou com erro. Veja data\logs\app.log
    pause
)

def test_saude(cliente):
    resposta = cliente.get("/saude")
    assert resposta.status_code == 200
    assert resposta.json() == {"status": "ok", "banco": "ok"}


def test_pagina_inicial_e_css(cliente):
    resposta = cliente.get("/")
    assert resposta.status_code == 200
    assert '<html lang="pt-BR">' in resposta.text

    css = cliente.get("/static/css/tokens.css")
    assert css.status_code == 200
    assert "--bg:         #0a0a0a;" in css.text


def test_404_amigavel(cliente):
    resposta = cliente.get("/nao-existe")
    assert resposta.status_code == 404
    assert "text/html" in resposta.headers["content-type"]
    assert "Página não encontrada" in resposta.text


def test_500_amigavel_e_registrado_no_log(app, cliente, settings):
    @app.get("/explode")
    def explode():
        raise RuntimeError("falha de teste")

    resposta = cliente.get("/explode")
    assert resposta.status_code == 500
    assert "Algo deu errado" in resposta.text
    assert "falha de teste" not in resposta.text

    log = (settings.logs_dir / "app.log").read_text(encoding="utf-8")
    assert "Erro não tratado em GET /explode" in log
    assert "RuntimeError: falha de teste" in log

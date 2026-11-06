import re

import pytest

from app.domain.competencia import Competencia
from app.navegacao import ITENS, item_ativo
from app.web import _svg_icone, eventos_htmx, icone


@pytest.mark.parametrize("item", ITENS, ids=lambda item: item.href)
def test_toda_pagina_da_navegacao_abre_com_item_ativo(cliente, item):
    resposta = cliente.get(item.href)
    assert resposta.status_code == 200
    ativos = re.findall(
        r'<a class="nav-item" href="([^"]+)"[^>]*aria-current="page"', resposta.text
    )
    assert ativos == [item.href]


def test_item_ativo_por_prefixo():
    assert item_ativo("/").href == "/"
    assert item_ativo("/lancamentos/novo").href == "/lancamentos"
    assert item_ativo("/lancamentosx") is None
    assert item_ativo("/nao-existe") is None


def test_seletor_de_mes_so_nas_paginas_de_financas(cliente):
    assert 'data-atalho="mes-anterior"' in cliente.get("/").text
    assert 'data-atalho="mes-anterior"' not in cliente.get("/categorias").text


def test_mes_padrao_e_o_atual(cliente):
    assert Competencia.atual().rotulo in cliente.get("/").text


def test_mes_pela_url_vira_cookie(cliente):
    resposta = cliente.get("/?mes=2026-03")
    assert "Março 2026" in resposta.text
    assert "mes=2026-02" in resposta.text  # link do mês anterior
    assert "mes=2026-04" in resposta.text  # link do próximo
    assert cliente.cookies.get("mes") == "2026-03"

    # Sem ?mes=, o cookie mantém o mês escolhido.
    assert "Março 2026" in cliente.get("/lancamentos").text


def test_mes_invalido_e_ignorado(cliente):
    resposta = cliente.get("/?mes=2026-13")
    assert resposta.status_code == 200
    assert Competencia.atual().rotulo in resposta.text
    assert "mes" not in cliente.cookies


def test_sidebar_recolhida_pelo_cookie(cliente):
    assert 'class="sidebar-recolhida"' not in cliente.get("/").text
    cliente.cookies.set("sidebar", "recolhida")
    assert '<html lang="pt-BR" class="sidebar-recolhida">' in cliente.get("/").text


def test_drawer_novo_lancamento_e_parcial(cliente):
    resposta = cliente.get("/lancamentos/novo", headers={"HX-Request": "true"})
    assert resposta.status_code == 200
    assert "<html" not in resposta.text
    assert 'id="drawer-titulo"' in resposta.text


def test_guia_visual(cliente):
    resposta = cliente.get("/design")
    assert resposta.status_code == 200
    texto = resposta.text
    assert "chart.umd.js" in texto and "charts.js" in texto
    assert 'data-grafico="barras"' in texto and 'data-grafico="linha"' in texto
    assert "Ver como tabela" in texto
    assert "R$ 8.120,00" in texto  # tabela do gráfico com valores formatados
    assert "▼ −1,8%" in re.sub(r"<[^>]+>|\s+", lambda m: " " if m[0].isspace() else "", texto)


def test_toast_pelo_servidor(cliente):
    resposta = cliente.post("/design/toast")
    assert resposta.status_code == 204
    assert '"desfazer": "/design/desfazer"' in resposta.headers["HX-Trigger"]


def test_eventos_htmx_e_ascii():
    cabecalho = eventos_htmx("Exclusão desfeita.", tipo="info")["HX-Trigger"]
    cabecalho.encode("latin-1")  # cabeçalhos HTTP não aceitam UTF-8 cru
    assert "\\u00e3" in cabecalho  # "ã" escapado


@pytest.mark.parametrize(
    "caminho",
    [
        "/static/vendor/htmx.min.js",
        "/static/vendor/chart.umd.js",
        "/static/js/app.js",
        "/static/js/charts.js",
        "/static/fonts/inter-latin-wght-normal.woff2",
    ],
)
def test_assets_locais(cliente, caminho):
    assert cliente.get(caminho).status_code == 200


def test_icone_inline_decorativo():
    svg = str(icone("wallet", "extra"))
    assert svg.startswith('<svg class="icone extra" aria-hidden="true" focusable="false"')
    assert "<!--" not in svg and "lucide" not in svg
    assert not re.search(r"\s(width|height)=", svg)


def test_icone_preserva_medidas_dos_elementos_internos():
    svg = str(icone("layout-dashboard"))
    abertura = svg[: svg.index(">")]
    assert not re.search(r"\s(width|height)=", abertura)
    assert svg.count('width="7"') == 4  # os quatro <rect> do ícone


def test_icone_rejeita_nome_suspeito():
    with pytest.raises(ValueError):
        _svg_icone("../../config")


def test_paginas_nao_dependem_de_internet(cliente):
    for item in ITENS:
        texto = cliente.get(item.href).text
        assert not re.search(r'(?:src|href)="https?://(?!127\.0\.0\.1|testserver)', texto)

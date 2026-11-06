"""Regressão visual do tema escuro (E7-03): compara com imagens de referência.

Só telas sem data nem valor que muda (o guia visual e estados vazios). Na primeira vez, ou
com `ATUALIZAR_REFERENCIAS=1`, grava a referência em vez de comparar. Se falhar, a imagem
obtida fica em `.resultado/`, ao lado das referências.
"""

import io
import os
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
from PIL import Image, ImageChops  # noqa: E402

pytestmark = pytest.mark.e2e

REFERENCIAS = Path(__file__).parent / "referencias"
TOLERANCIA = 0.002  # fração de pixels que podem mudar (antisserrilhado)
SENSIBILIDADE = 16  # diferença por canal abaixo disso não conta

TELAS = [
    ("guia-visual-desktop", "/design", 1440, 900),
    ("guia-visual-celular", "/design", 390, 844),
    ("carteira-vazia", "/carteira", 1440, 900),
    ("categorias", "/categorias", 1440, 900),
]


def diferenca(obtida: Image.Image, referencia: Image.Image) -> float:
    """Fração de pixels diferentes; 1.0 se os tamanhos não baterem."""
    if obtida.size != referencia.size:
        return 1.0
    delta = ImageChops.difference(obtida.convert("RGB"), referencia.convert("RGB")).convert("L")
    mudados = sum(delta.histogram()[SENSIBILIDADE:])
    return mudados / (obtida.width * obtida.height)


def test_medida_de_diferenca():
    preta = Image.new("RGB", (10, 10), "black")
    assert diferenca(preta, preta.copy()) == 0
    assert diferenca(preta, Image.new("RGB", (10, 10), "white")) == 1
    assert diferenca(preta, Image.new("RGB", (5, 5), "black")) == 1


@pytest.mark.parametrize(("nome", "caminho", "largura", "altura"), TELAS)
def test_tela_igual_a_referencia(navegador, url_vazio, nome, caminho, largura, altura):
    contexto = navegador.new_context(
        viewport={"width": largura, "height": altura}, reduced_motion="reduce", locale="pt-BR"
    )
    pagina = contexto.new_page()
    pagina.goto(url_vazio + caminho)
    pagina.wait_for_load_state("networkidle")
    pagina.evaluate("document.fonts.ready")
    # O status de cotações mostra "ainda não atualizadas" e não dispara rede sem ativos.
    bruto = pagina.screenshot(full_page=True, animations="disabled", caret="hide")
    contexto.close()
    obtida = Image.open(io.BytesIO(bruto))

    arquivo = REFERENCIAS / f"{nome}.png"
    if os.environ.get("ATUALIZAR_REFERENCIAS") or not arquivo.exists():
        REFERENCIAS.mkdir(exist_ok=True)
        arquivo.write_bytes(bruto)
        return
    medida = diferenca(obtida, Image.open(arquivo))
    if medida > TOLERANCIA:
        resultado = REFERENCIAS / ".resultado"
        resultado.mkdir(exist_ok=True)
        (resultado / f"{nome}.png").write_bytes(bruto)
    assert medida <= TOLERANCIA, f"{nome}: {medida:.2%} dos pixels mudaram"

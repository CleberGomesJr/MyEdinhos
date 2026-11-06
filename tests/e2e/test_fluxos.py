"""Fluxos principais no navegador real (E7-03)."""

import re

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

pytestmark = pytest.mark.e2e

PAGINAS = [
    "/", "/lancamentos", "/lancamentos/recorrencias", "/contas", "/cartoes", "/orcamento",
    "/relatorios", "/categorias", "/carteira", "/proventos", "/mercado", "/analise",
    "/analise/alocacao",
    "/analise/objetivos", "/analise/regras", "/analise/snapshot", "/config", "/assistente/contas",
    "/assistente/cartoes", "/assistente/carteira", "/design",
]  # fmt: skip


def _carregada(pagina) -> None:
    pagina.wait_for_load_state("networkidle")


def _kpi(pagina, rotulo: str):
    titulo = pagina.locator(".kpi-rotulo", has_text=re.compile(rf"^\s*{rotulo}\s*$"))
    return pagina.locator(".kpi", has=titulo)


def _salvar(pagina) -> None:
    # O robô é mais rápido que o HTMX: só clica depois que ele assumiu o formulário novo.
    pagina.wait_for_function("!!document.querySelector('#drawer form')?.['htmx-internal-data']")
    pagina.locator("#drawer button.ordem-ultima").click()


def test_todas_as_telas_abrem_sem_erro_e_no_tema_escuro(aba, url_demo):
    """Inclui o CSP: um script ou estilo bloqueado aparece como erro no console."""
    for caminho in PAGINAS:
        aba.goto(url_demo + caminho)
        _carregada(aba)
        assert aba.evaluate("!!window.htmx"), caminho
        fundo = aba.evaluate("getComputedStyle(document.body).backgroundColor")
        assert fundo == "rgb(10, 10, 10)", caminho
        largura = aba.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
        assert largura, f"{caminho}: rolagem horizontal"
    assert aba.erros == []


def test_graficos_desenham_com_o_csp(aba, url_demo):
    aba.goto(url_demo + "/")
    _carregada(aba)
    canvases = aba.locator("canvas[data-grafico]")
    expect(canvases.first).to_be_visible()
    assert aba.evaluate(
        "[...document.querySelectorAll('canvas[data-grafico]')].every(c => !!Chart.getChart(c))"
    )
    assert aba.erros == []


def test_celular_sem_rolagem_horizontal(navegador, url_demo):
    contexto = navegador.new_context(
        viewport={"width": 390, "height": 844}, reduced_motion="reduce", is_mobile=True
    )
    pagina = contexto.new_page()
    for caminho in PAGINAS:
        pagina.goto(url_demo + caminho)
        _carregada(pagina)
        excesso = pagina.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert excesso <= 0, f"{caminho}: {excesso}px além da tela"
    contexto.close()


def test_lancar_despesa_aparece_no_dashboard_e_desfazer(aba, url_vazio):
    aba.goto(url_vazio + "/lancamentos")
    _carregada(aba)
    aba.keyboard.press("n")  # atalho do novo lançamento
    valor = aba.locator("#drawer [name=valor]")
    expect(valor).to_be_focused()
    aba.keyboard.type("123,45")
    aba.keyboard.press("Tab")
    aba.keyboard.type("Feira de domingo")
    aba.locator("#drawer select[name=categoria_id]:visible").select_option(label="Mercado")
    _salvar(aba)

    expect(aba.locator("#drawer")).not_to_be_visible()
    linha = aba.locator("#lista tbody tr", has_text="Feira de domingo")
    expect(linha).to_contain_text("R$ 123,45")

    aba.goto(url_vazio + "/")
    _carregada(aba)
    expect(_kpi(aba, "Saídas")).to_contain_text("R$ 123,45")
    expect(aba.get_by_text("Feira de domingo")).to_be_visible()

    # Editar → excluir → Desfazer, voltando ao dashboard atualizado sem recarregar.
    aba.get_by_role("button", name="Feira de domingo").click()
    aba.locator("#drawer").get_by_role("button", name="Excluir").first.click()
    aba.locator("#confirmacao-ok").click()
    expect(_kpi(aba, "Saídas")).to_contain_text("R$ 0,00")
    aba.locator(".toast").get_by_role("button", name="Desfazer").click()
    expect(_kpi(aba, "Saídas")).to_contain_text("R$ 123,45")
    assert aba.erros == []


def test_digitacao_rapida_nao_cai_em_outro_campo(navegador, url_vazio):
    """Regressão: o foco voltava ao Valor depois de 20 ms e gravava um valor errado."""
    for _ in range(12):  # contexto novo a cada vez: o defeito aparecia com o cache frio
        contexto = navegador.new_context(reduced_motion="reduce")
        pagina = contexto.new_page()
        pagina.goto(url_vazio + "/lancamentos")
        _carregada(pagina)
        pagina.keyboard.press("n")
        expect(pagina.locator("#drawer [name=valor]")).to_be_focused()
        pagina.keyboard.type("123,45")
        pagina.keyboard.press("Tab")
        pagina.keyboard.type("Feira de domingo")
        formulario = pagina.locator("#drawer form")
        expect(formulario.locator("[name=valor]")).to_have_value("123,45")
        expect(formulario.locator("[name=descricao]")).to_have_value("Feira de domingo")
        contexto.close()


def test_erro_de_formulario_fica_no_drawer(aba, url_vazio):
    aba.goto(url_vazio + "/lancamentos")
    _carregada(aba)
    aba.keyboard.press("n")
    expect(aba.locator("#drawer [name=valor]")).to_be_focused()
    _salvar(aba)
    expect(aba.locator("#drawer .campo-erro").first).to_be_visible()
    expect(aba.locator("#drawer")).to_be_visible()
    # 422 é o retorno esperado da validação; não é erro do app.
    assert [e for e in aba.erros if "422" not in e] == []


def test_carteira_do_cadastro_ao_aporte(aba, url_vazio):
    aba.goto(url_vazio + "/carteira")
    _carregada(aba)
    aba.locator("button", has_text="Posição inicial").first.click()
    aba.locator("#drawer [name=linhas]").fill("ITSA4; 100; 9,50\nMXRF11; 300; 10,12")
    _salvar(aba)
    expect(aba.locator("#lista tbody tr", has_text="MXRF11")).to_contain_text("R$ 3.036,00")
    expect(_kpi(aba, "Aportado")).to_contain_text("R$ 3.986,00")

    aba.goto(url_vazio + "/analise/alocacao")
    _carregada(aba)
    aba.get_by_role("button", name="Editar alocação-alvo").click()
    aba.locator("[data-preencher-sugestao]").click()
    _salvar(aba)
    expect(aba.locator("#lista tbody tr", has_text="FIIs")).to_contain_text("20%")

    aba.locator("#campo-aporte").fill("400,00")
    aba.locator(".formulario-aporte button[type=submit]").click()
    expect(aba.locator("#resultado-aporte")).to_contain_text("Renda fixa")
    assert aba.erros == []


def test_importar_e_exportar_no_navegador(aba, url_vazio, tmp_path):
    aba.goto(url_vazio + "/config")
    _carregada(aba)
    with aba.expect_download() as download:
        aba.get_by_role("link", name="Baixar .zip").click()
    arquivo = tmp_path / "exportado.zip"
    download.value.save_as(arquivo)

    aba.locator("#campo-arquivo").set_input_files(arquivo)
    aba.get_by_role("button", name="Importar").click()
    aba.locator("#confirmacao-ok").click()
    expect(aba.get_by_text("Dados importados.")).to_be_visible()
    expect(aba.locator("#lista")).to_contain_text("antes-de-importar")
    assert aba.erros == []


def test_cadastro_inicial_salva_em_formulario_comum(aba, url_vazio):
    """Formulário sem HTMX: o navegador manda `Origin` conforme a Referrer-Policy. Com
    `no-referrer` vira `Origin: null`, e a proteção contra outros sites recusa o envio."""
    aba.goto(url_vazio + "/assistente/contas")
    with aba.expect_navigation() as navegacao:
        aba.locator("form[method=post] button[type=submit]").click()
    assert navegacao.value.status == 200
    expect(aba).to_have_url(re.compile(r"/assistente/cartoes"))


def test_importar_extrato_no_navegador(aba, url_vazio, tmp_path):
    arquivo = tmp_path / "extrato.csv"
    arquivo.write_text(
        "Banco Exemplo\n\nData;Histórico;Valor\n"
        "01/10/2026;Salário da empresa;5.000,00\n"
        "03/10/2026;Padaria da esquina;-23,45\n"
        "05/10/2026;Pagamento de fatura;-100,00\n",
        encoding="cp1252",
    )
    aba.goto(url_vazio + "/lancamentos")
    aba.get_by_role("link", name="Importar extrato").click()
    aba.locator("#campo-arquivo").set_input_files(arquivo)
    aba.locator("form[method=post] button[type=submit]").click()
    expect(aba.get_by_text("O que é cada coluna?")).to_be_visible()
    expect(aba.locator("#campo-col_data")).to_have_value("Data")
    expect(aba.locator("#campo-col_valor")).to_have_value("Valor")
    aba.get_by_role("button", name="Revisar").click()

    expect(aba.get_by_text("Padaria da esquina")).to_be_visible()
    fatura = aba.get_by_role("checkbox", name="Importar Pagamento de fatura")
    expect(fatura).not_to_be_checked()
    aba.get_by_role("button", name="Importar marcadas").click()
    expect(aba.get_by_text("2 lançamento(s) importado(s)")).to_be_visible()

    aba.get_by_role("link", name="Ver lançamentos").click()
    expect(aba.get_by_text("Padaria da esquina")).to_be_visible()

    # O mesmo arquivo de novo não duplica; e a última importação sai de uma vez.
    aba.goto(url_vazio + "/lancamentos/importar")
    aba.locator("#campo-arquivo").set_input_files(arquivo)
    aba.locator("form[method=post] button[type=submit]").click()
    aba.get_by_role("button", name="Revisar").click()
    expect(aba.get_by_text("Já importada antes")).to_have_count(2)

    aba.goto(url_vazio + "/lancamentos/importar")
    aba.get_by_role("button", name="Desfazer").click()
    aba.locator("#confirmacao-ok").click()
    expect(aba.locator(".aviso", has_text="criou")).to_have_count(0)


def test_importar_operacoes_no_navegador(aba, url_vazio, tmp_path):
    arquivo = tmp_path / "negociacao.csv"
    arquivo.write_text(
        "Data do Negócio;Tipo de Movimentação;Mercado;Instituição;Código de Negociação;"
        "Quantidade;Preço;Valor\n"
        "05/10/2026;Venda;Vista;CORRETORA;BBAS3;40;R$ 11,00;R$ 440,00\n"
        "02/10/2026;Compra;Fracionário;CORRETORA;BBAS3F;50;R$ 10,00;R$ 500,00\n"
        "06/10/2026;Venda;Vista;CORRETORA;WEGE3;1;R$ 40,00;R$ 40,00\n",
        encoding="utf-8",
    )
    aba.goto(url_vazio + "/carteira")
    aba.get_by_role("link", name="Importar operações").click()
    aba.locator("#campo-arquivo").set_input_files(arquivo)
    aba.locator("form[method=post] button[type=submit]").click()
    aba.get_by_role("button", name="Revisar").click()
    # A venda sem posição já aparece na revisão, sem caixa de marcar.
    expect(aba.get_by_text("Não há WEGE3 na carteira nessa data.")).to_be_visible()
    aba.get_by_role("button", name="Importar marcadas").click()
    expect(aba.get_by_text("2 operação(ões) importada(s)")).to_be_visible()
    aba.get_by_role("link", name="Ver carteira").click()
    expect(aba.get_by_role("link", name="BBAS3").first).to_be_visible()

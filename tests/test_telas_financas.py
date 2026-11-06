"""Telas e rotas de contas, categorias, lançamentos e recorrências (HTTP + HTMX)."""

import json
import re

from sqlalchemy import select

from app.db.models import Categoria, Conta, Lancamento, Recorrencia
from app.domain.competencia import Competencia
from app.domain.datas import hoje

HTMX = {"HX-Request": "true"}


def _eventos(resposta) -> dict:
    return json.loads(resposta.headers.get("HX-Trigger", "{}"))


def _ids(sessao):
    conta = sessao.scalar(select(Conta))
    mercado = sessao.scalar(select(Categoria).where(Categoria.nome == "Mercado"))
    return conta.id, mercado.id


def _novo(cliente, sessao, **campos):
    conta_id, mercado_id = _ids(sessao)
    dados = {
        "tipo": "saida",
        "valor": "45,90",
        "descricao": "Feira",
        "data": hoje().isoformat(),
        "conta_id": conta_id,
        "categoria_id": mercado_id,
        "pago": "1",
        "repeticao": "nao",
    }
    dados.update(campos)
    return cliente.post("/lancamentos", data=dados, headers=HTMX)


def test_paginas_do_e2_abrem(cliente):
    for caminho in ("/lancamentos", "/contas", "/categorias", "/lancamentos/recorrencias"):
        resposta = cliente.get(caminho)
        assert resposta.status_code == 200, caminho
        assert "Em construção" not in resposta.text


def test_lista_vazia_tem_estado_vazio(cliente):
    assert "Nada lançado em" in cliente.get("/lancamentos").text


def test_drawer_novo_lancamento_tem_padroes(cliente):
    texto = cliente.get("/lancamentos/novo", headers=HTMX).text
    assert 'name="valor"' in texto and "autofocus" in texto
    assert f'value="{hoje().isoformat()}"' in texto
    assert re.search(r'<option value="\d+" selected>Conta Teste</option>', texto)
    assert "Salvar e adicionar outro" in texto


def test_criar_lancamento_fecha_drawer_e_atualiza(cliente, sessao):
    resposta = _novo(cliente, sessao)
    assert resposta.status_code == 204
    eventos = _eventos(resposta)
    assert eventos["toast"]["mensagem"] == "“Feira” salvo."
    assert eventos["fechar-drawer"] and eventos["atualizar"]
    texto = cliente.get("/lancamentos").text
    assert "Feira" in texto and "−R$ 45,90" in texto


def test_salvar_e_adicionar_outro_mantem_tipo_conta_e_data(cliente, sessao):
    resposta = _novo(cliente, sessao, continuar="1", data="2026-10-03")
    assert resposta.status_code == 200
    assert "fechar-drawer" not in _eventos(resposta)
    assert _eventos(resposta)["atualizar"]
    assert 'value="2026-10-03"' in resposta.text
    assert 'name="valor"' in resposta.text and 'value="45,90"' not in resposta.text


def test_erros_voltam_no_formulario_com_422(cliente, sessao):
    resposta = _novo(cliente, sessao, valor="", descricao="")
    assert resposta.status_code == 422
    assert resposta.text.count("Obrigatório.") == 2
    assert 'aria-invalid="true"' in resposta.text
    assert sessao.scalar(select(Lancamento)) is None


def test_editar_excluir_e_desfazer(cliente, sessao):
    _novo(cliente, sessao)
    ident = sessao.scalar(select(Lancamento.id))
    formulario = cliente.get(f"/lancamentos/{ident}/editar", headers=HTMX).text
    assert 'value="45,90"' in formulario and "Excluir" in formulario

    conta_id, mercado_id = _ids(sessao)
    editado = cliente.post(
        f"/lancamentos/{ident}",
        data={"tipo": "saida", "valor": "50,00", "descricao": "Feira grande", "data": "2026-10-04",
              "conta_id": conta_id, "categoria_id": mercado_id, "pago": "1"},
        headers=HTMX,
    )  # fmt: skip
    assert editado.status_code == 204
    sessao.expire_all()
    assert sessao.get(Lancamento, ident).valor_centavos == 5000

    excluido = cliente.delete(f"/lancamentos/{ident}", headers=HTMX)
    desfazer = _eventos(excluido)["toast"]["desfazer"]
    sessao.expire_all()
    assert sessao.get(Lancamento, ident) is None

    restaurado = cliente.post(desfazer, headers=HTMX)
    assert restaurado.status_code == 204
    assert "restaurado" in _eventos(restaurado)["toast"]["mensagem"]
    sessao.expire_all()
    assert sessao.get(Lancamento, ident).descricao == "Feira grande"


def test_pendente_atrasado_e_pagar(cliente, sessao):
    _novo(cliente, sessao, pago="", data="2026-01-10", descricao="Boleto velho")
    pagina = cliente.get("/lancamentos").text
    assert "1 lançamento atrasado" in pagina

    atrasados = cliente.get("/lancamentos?situacao=atrasados").text
    assert "Boleto velho" in atrasados and "Atrasado" in atrasados

    lancamento = sessao.scalar(select(Lancamento))
    resposta = cliente.post(f"/lancamentos/{lancamento.id}/pagar", headers=HTMX)
    assert resposta.status_code == 204
    assert "fechar-drawer" not in _eventos(resposta)
    assert "lançamento atrasado" not in cliente.get("/lancamentos").text


def test_filtros_pela_url(cliente, sessao):
    _novo(cliente, sessao, descricao="Padaria")
    _novo(cliente, sessao, descricao="Açougue")
    texto = cliente.get("/lancamentos?busca=acougue").text
    assert "Açougue" in texto and "Padaria" not in texto
    assert "Limpar" in texto


def test_parcelado_pela_tela(cliente, sessao):
    resposta = _novo(cliente, sessao, valor="300,00", repeticao="parcelado", parcelas="3")
    assert _eventos(resposta)["toast"]["mensagem"] == "3 parcelas de “Feira” criadas."
    assert len(sessao.scalars(select(Lancamento)).all()) == 3


def test_recorrencia_pela_tela_gera_meses_seguintes(cliente, sessao):
    _novo(cliente, sessao, descricao="Academia", repeticao="mensal")
    proximo = Competencia.atual().proxima()
    texto = cliente.get(f"/lancamentos?mes={proximo}").text
    assert "Academia" in texto
    regra = sessao.scalar(select(Recorrencia))
    assert "Academia" in cliente.get("/lancamentos/recorrencias").text

    encerrada = cliente.post(f"/lancamentos/recorrencias/{regra.id}/encerrar", headers=HTMX)
    assert encerrada.status_code == 204
    assert "Encerrada em" in cliente.get("/lancamentos/recorrencias").text


def test_transferencia_pela_tela(cliente, sessao):
    criada = cliente.post(
        "/contas",
        data={"nome": "Poupança", "tipo": "poupanca", "saldo_inicial": "0,00",
              "data_saldo_inicial": "2026-01-01"},
        headers=HTMX,
    )  # fmt: skip
    assert criada.status_code == 204
    poupanca = sessao.scalar(select(Conta).where(Conta.nome == "Poupança"))
    resposta = _novo(cliente, sessao, tipo="transferencia", valor="100,00", descricao="Guardar",
                     conta_destino_id=poupanca.id)  # fmt: skip
    assert resposta.status_code == 204
    texto = cliente.get("/lancamentos").text
    assert "Conta Teste → Poupança" in texto


def test_conta_criar_editar_arquivar(cliente, sessao):
    resposta = cliente.post(
        "/contas",
        data={"nome": "Carteira", "tipo": "dinheiro", "saldo_inicial": "-12,50",
              "data_saldo_inicial": "2026-10-01"},
        headers=HTMX,
    )  # fmt: skip
    assert resposta.status_code == 204
    conta = sessao.scalar(select(Conta).where(Conta.nome == "Carteira"))
    assert conta.saldo_inicial_centavos == -1250
    assert 'value="-12,50"' in cliente.get(f"/contas/{conta.id}/editar", headers=HTMX).text

    repetida = cliente.post(
        "/contas",
        data={"nome": "carteira", "tipo": "dinheiro", "data_saldo_inicial": "2026-10-01"},
        headers=HTMX,
    )
    assert repetida.status_code == 422 and "Já existe uma conta" in repetida.text

    cliente.post(f"/contas/{conta.id}/arquivar", headers=HTMX)
    assert "Arquivadas" in cliente.get("/contas").text


def test_categoria_criar_subcategoria_excluir_e_desfazer(cliente, sessao):
    moradia = sessao.scalar(select(Categoria).where(Categoria.nome == "Moradia"))
    resposta = cliente.post(
        "/categorias",
        data={"nome": "Condomínio", "tipo": "saida", "pai_id": moradia.id, "icone": "house"},
        headers=HTMX,
    )
    assert resposta.status_code == 204
    texto = cliente.get("/categorias").text
    assert "Condomínio" in texto and 'class="subcategoria"' in texto

    nova = sessao.scalar(select(Categoria).where(Categoria.nome == "Condomínio"))
    assert nova.cor_slot == moradia.cor_slot
    excluida = cliente.post(f"/categorias/{nova.id}/excluir", headers=HTMX)
    desfazer = _eventos(excluida)["toast"]["desfazer"]
    assert "Condomínio" not in cliente.get("/categorias").text
    cliente.post(desfazer, headers=HTMX)
    assert "Condomínio" in cliente.get("/categorias").text


def test_categoria_sem_cor_e_recusada(cliente):
    resposta = cliente.post(
        "/categorias", data={"nome": "Pets", "tipo": "saida", "icone": "paw-print"}, headers=HTMX
    )
    assert resposta.status_code == 422 and "Obrigatório." in resposta.text


def test_editar_inexistente_da_404(cliente):
    assert cliente.get("/lancamentos/999/editar", headers=HTMX).status_code == 404
    assert cliente.post("/desfazer/999", headers=HTMX).status_code == 204  # toast de erro


def test_listas_nao_vazam_atributos_htmx_para_os_filhos(cliente):
    """#lista usa hx-select/hx-swap próprios; sem hx-disinherit, os botões de editar dentro
    dela herdariam esses atributos e apagariam o conteúdo do drawer."""
    for caminho in ("/lancamentos", "/contas", "/categorias", "/lancamentos/recorrencias"):
        assert '<div id="lista" hx-disinherit="*"' in cliente.get(caminho).text, caminho

"""Regras de contas, categorias, lançamentos, parcelas, recorrências e desfazer (E2-14)."""

from datetime import date

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.migracoes import aplicar_migracoes
from app.db.models import Categoria, Conta, Lancamento, Recorrencia, TipoCategoria
from app.domain import categorias, contas, lancamentos, recorrencias
from app.domain.competencia import Competencia
from app.domain.datas import somar_meses
from app.domain.desfazer import desfazer
from app.domain.formularios import ErroValidacao
from app.domain.lancamentos import Filtros, Situacao

OUTUBRO = Competencia(2026, 10)
HOJE = date(2026, 10, 26)


def _categoria(sessao, nome: str) -> Categoria:
    return sessao.scalar(select(Categoria).where(Categoria.nome == nome))


@pytest.fixture
def pessoal(sessao) -> Conta:
    conta = sessao.scalar(select(Conta).where(Conta.nome == "Conta Teste"))
    contas.salvar(
        sessao,
        {"nome": "Conta Teste", "tipo": "corrente", "saldo_inicial": "1.000,00",
         "data_saldo_inicial": "2026-10-01"},
        conta,
    )  # fmt: skip
    return conta


@pytest.fixture
def poupanca(sessao) -> Conta:
    return contas.salvar(
        sessao,
        {"nome": "Poupança", "tipo": "poupanca", "saldo_inicial": "0",
         "data_saldo_inicial": "2026-10-01"},
    )  # fmt: skip


def _lancar(sessao, conta, **campos) -> list[Lancamento]:
    dados = {
        "tipo": "saida",
        "valor": "10,00",
        "descricao": "Teste",
        "data": "2026-10-10",
        "conta_id": str(conta.id),
        "categoria_id": str(_categoria(sessao, "Mercado").id),
        "pago": "1",
    }
    dados.update({chave: str(valor) for chave, valor in campos.items()})
    if dados["tipo"] == "entrada" and "categoria_id" not in campos:
        dados["categoria_id"] = str(_categoria(sessao, "Salário").id)
    return lancamentos.salvar(sessao, dados)


# ───────── Seed (E2-02) ─────────


def test_banco_novo_so_tem_categorias_padrao(tmp_path):
    settings = Settings(data_dir=tmp_path / "novo")
    settings.garantir_pastas()
    aplicar_migracoes(settings.database_url)
    motor = create_engine(settings.database_url)
    with Session(motor) as sessao:
        assert _categoria(sessao, "Moradia").tipo == TipoCategoria.SAIDA
        assert _categoria(sessao, "Salário").tipo == TipoCategoria.ENTRADA
        assert _categoria(sessao, "Outros gastos").cor_slot == 0
        for modelo in (Conta, Lancamento):
            assert sessao.scalar(select(func.count()).select_from(modelo)) == 0
    motor.dispose()


# ───────── Saldos e transferências ─────────


def test_saldo_entrada_saida_e_pendente(sessao, pessoal):
    _lancar(sessao, pessoal, tipo="entrada", valor="500,00")
    _lancar(sessao, pessoal, valor="120,50")
    _lancar(sessao, pessoal, valor="99,00", pago="")  # pendente
    assert contas.saldos(sessao, HOJE)[pessoal.id] == 100000 + 50000 - 12050
    assert contas.saldos(sessao, HOJE, incluir_pendentes=True)[pessoal.id] == 137950 - 9900


def test_saldo_ignora_lancamentos_antes_do_saldo_inicial(sessao, pessoal):
    _lancar(sessao, pessoal, valor="300,00", data="2026-09-30")
    assert contas.saldos(sessao, HOJE)[pessoal.id] == 100000


def test_saldo_respeita_data_limite(sessao, pessoal):
    _lancar(sessao, pessoal, valor="50,00", data="2026-10-27")
    assert contas.saldos(sessao, HOJE)[pessoal.id] == 100000
    assert contas.saldos(sessao, date(2026, 10, 31))[pessoal.id] == 95000


def test_transferencia_move_saldo_e_nao_entra_nos_totais(sessao, pessoal, poupanca):
    feitos = _lancar(
        sessao, pessoal, tipo="transferencia", valor="200,00", conta_destino_id=poupanca.id,
        categoria_id="",
    )  # fmt: skip
    saldos = contas.saldos(sessao, HOJE)
    assert saldos[pessoal.id] == 80000
    assert saldos[poupanca.id] == 20000
    assert feitos[0].categoria_id is None
    resumo = lancamentos.totais(feitos)
    assert (resumo.entradas, resumo.saidas) == (0, 0)


def test_transferencia_para_a_mesma_conta_e_recusada(sessao, pessoal):
    with pytest.raises(ErroValidacao) as erro:
        _lancar(sessao, pessoal, tipo="transferencia", conta_destino_id=pessoal.id)
    assert "conta_destino_id" in erro.value.erros


# ───────── Validação ─────────


def test_validacao_por_campo(sessao, pessoal):
    with pytest.raises(ErroValidacao) as erro:
        _lancar(sessao, pessoal, valor="0", descricao="", data="ontem")
    assert set(erro.value.erros) == {"valor", "descricao", "data"}


def test_categoria_precisa_ser_do_tipo_do_lancamento(sessao, pessoal):
    with pytest.raises(ErroValidacao) as erro:
        _lancar(sessao, pessoal, tipo="entrada", categoria_id=_categoria(sessao, "Mercado").id)
    assert erro.value.erros["categoria_id"] == "Escolha uma categoria de entrada."


def test_conta_arquivada_nao_recebe_lancamento_novo(sessao, pessoal):
    contas.definir_ativa(sessao, pessoal, False)
    with pytest.raises(ErroValidacao):
        _lancar(sessao, pessoal)


def test_conta_com_nome_repetido(sessao, pessoal):
    with pytest.raises(ErroValidacao) as erro:
        contas.salvar(
            sessao,
            {"nome": "conta TESTE", "tipo": "corrente", "data_saldo_inicial": "2026-10-01"},
        )
    assert "nome" in erro.value.erros


# ───────── Lista, filtros e totais (E2-05 / E2-09) ─────────


def test_lista_do_mes_filtros_e_totais(sessao, pessoal):
    _lancar(sessao, pessoal, tipo="entrada", valor="1.000,00", descricao="Salário")
    _lancar(sessao, pessoal, valor="200,00", descricao="Pão de Açúcar")
    _lancar(sessao, pessoal, valor="50,00", descricao="Luz", pago="",
            categoria_id=_categoria(sessao, "Moradia").id)  # fmt: skip
    _lancar(sessao, pessoal, valor="70,00", data="2026-11-02")  # outro mês

    todos = lancamentos.listar(sessao, OUTUBRO, Filtros(), HOJE)
    assert len(todos) == 3
    resumo = lancamentos.totais(todos)
    assert (resumo.entradas, resumo.saidas, resumo.resultado, resumo.pendentes) == (
        100000, 25000, 75000, 1,
    )  # fmt: skip

    busca = lancamentos.listar(sessao, OUTUBRO, Filtros(busca="acucar"), HOJE)
    assert [item.descricao for item in busca] == ["Pão de Açúcar"]
    a_pagar = lancamentos.listar(sessao, OUTUBRO, Filtros(situacao=Situacao.A_PAGAR), HOJE)
    assert [item.descricao for item in a_pagar] == ["Luz"]


def test_filtro_por_categoria_inclui_subcategorias(sessao, pessoal):
    moradia = _categoria(sessao, "Moradia")
    aluguel = categorias.salvar(
        sessao, {"nome": "Aluguel", "tipo": "saida", "pai_id": str(moradia.id), "cor_slot": "5"}
    )
    _lancar(sessao, pessoal, categoria_id=aluguel.id)
    filtrado = lancamentos.listar(sessao, OUTUBRO, Filtros(categoria_id=moradia.id), HOJE)
    assert len(filtrado) == 1


def test_atrasados_de_qualquer_mes(sessao, pessoal):
    _lancar(sessao, pessoal, descricao="Boleto antigo", data="2026-09-10", pago="")
    _lancar(sessao, pessoal, descricao="Vence depois", pago="", vencimento="2026-11-05")
    _lancar(sessao, pessoal, descricao="Venceu ontem", pago="", vencimento="2026-10-25")
    atrasados = lancamentos.listar(sessao, OUTUBRO, Filtros(situacao=Situacao.ATRASADOS), HOJE)
    assert {item.descricao for item in atrasados} == {"Boleto antigo", "Venceu ontem"}
    assert lancamentos.contar_atrasados(sessao, HOJE) == 2


def test_filtros_da_url_toleram_lixo():
    filtros = Filtros.de_parametros(
        {"conta_id": "abc", "tipo": "x", "situacao": "?", "busca": " a "}
    )
    assert filtros == Filtros(busca="a")


# ───────── Parcelas (E2-11) ─────────


def test_dividir_valor_sem_perder_centavos():
    assert lancamentos.dividir_valor(10000, 3) == [3334, 3333, 3333]
    assert sum(lancamentos.dividir_valor(99999, 7)) == 99999


def test_parcelado_cria_uma_por_mes(sessao, pessoal):
    parcelas = _lancar(sessao, pessoal, valor="100,00", data="2026-01-31", repeticao="parcelado",
                       parcelas="3")  # fmt: skip
    assert [p.data for p in parcelas] == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]
    assert [p.competencia for p in parcelas] == ["2026-01", "2026-02", "2026-03"]
    assert [p.valor_centavos for p in parcelas] == [3334, 3333, 3333]
    assert [p.pago for p in parcelas] == [True, False, False]
    assert {(p.parcela_n, p.parcela_total) for p in parcelas} == {(1, 3), (2, 3), (3, 3)}
    assert len({p.parcela_grupo_id for p in parcelas}) == 1


def test_parcelas_valor_minimo(sessao, pessoal):
    with pytest.raises(ErroValidacao) as erro:
        _lancar(sessao, pessoal, valor="0,02", repeticao="parcelado", parcelas="3")
    assert "valor" in erro.value.erros


def test_excluir_esta_e_as_proximas_parcelas_e_desfazer(sessao, pessoal):
    parcelas = _lancar(sessao, pessoal, valor="40,00", repeticao="parcelado", parcelas="4")
    registro = lancamentos.excluir(sessao, parcelas[1], escopo="proximas")
    restantes = sessao.scalars(select(Lancamento.parcela_n)).all()
    assert restantes == [1]
    assert "mais 2 parcela(s)" in registro.descricao

    assert desfazer(sessao, registro.id) is not None
    assert sorted(sessao.scalars(select(Lancamento.parcela_n))) == [1, 2, 3, 4]
    assert desfazer(sessao, registro.id) is None  # já desfeito


def test_desfazer_reinsere_com_o_mesmo_id_e_tipos(sessao, pessoal):
    (original,) = _lancar(sessao, pessoal, valor="12,34", vencimento="2026-10-30", pago="")
    ident = original.id
    registro = lancamentos.excluir(sessao, original)
    sessao.expire_all()
    assert sessao.get(Lancamento, ident) is None
    desfazer(sessao, registro.id)
    volta = sessao.get(Lancamento, ident)
    assert (volta.valor_centavos, volta.vencimento, volta.pago) == (1234, date(2026, 10, 30), False)


# ───────── Recorrências (E2-10) ─────────


def test_ocorrencias_mensais_no_fim_do_mes():
    datas = recorrencias.ocorrencias(
        recorrencias.Frequencia.MENSAL, date(2026, 1, 31), date(2026, 1, 31), date(2026, 4, 30)
    )
    assert datas == [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]


def test_ocorrencias_semanais_e_anuais():
    semanal = recorrencias.ocorrencias(
        recorrencias.Frequencia.SEMANAL, date(2026, 10, 1), date(2026, 10, 1), date(2026, 10, 31)
    )
    assert semanal == [
        date(2026, 10, 8),
        date(2026, 10, 15),
        date(2026, 10, 22),
        date(2026, 10, 29),
    ]
    anual = recorrencias.ocorrencias(
        recorrencias.Frequencia.ANUAL, date(2024, 2, 29), date(2024, 2, 29), date(2026, 3, 1)
    )
    assert anual == [date(2025, 2, 28), date(2026, 2, 28)]


def test_recorrencia_gera_pendentes_sem_duplicar_nem_ressuscitar(sessao, pessoal):
    (primeiro,) = _lancar(sessao, pessoal, descricao="Aluguel", valor="1.500,00",
                          data="2026-10-05", repeticao="mensal")  # fmt: skip
    assert primeiro.recorrencia_id is not None and primeiro.pago

    assert recorrencias.gerar_pendentes(sessao, date(2026, 12, 31)) == 2
    assert recorrencias.gerar_pendentes(sessao, date(2026, 12, 31)) == 0  # idempotente

    novembro = sessao.scalar(select(Lancamento).where(Lancamento.data == date(2026, 11, 5)))
    assert not novembro.pago and novembro.vencimento == date(2026, 11, 5)
    lancamentos.excluir(sessao, novembro)
    recorrencias.gerar_pendentes(sessao, date(2027, 1, 31))
    datas = sorted(sessao.scalars(select(Lancamento.data).where(Lancamento.descricao == "Aluguel")))
    assert datas == [date(2026, 10, 5), date(2026, 12, 5), date(2027, 1, 5)]


def test_atualizar_e_encerrar_recorrencia(sessao, pessoal):
    _lancar(sessao, pessoal, descricao="Streaming", valor="39,90", data="2026-10-20",
            repeticao="mensal", categoria_id=_categoria(sessao, "Assinaturas").id)  # fmt: skip
    regra = sessao.scalar(select(Recorrencia))
    recorrencias.gerar_pendentes(sessao, date(2027, 1, 31))

    mudados = recorrencias.atualizar(
        sessao, regra,
        {"descricao": "Streaming 4K", "valor": "55,90", "categoria_id": str(regra.categoria_id)},
        HOJE,
    )  # fmt: skip
    assert mudados == 3  # nov, dez, jan (outubro já foi pago)
    assert recorrencias.proxima(regra, HOJE) == date(2026, 11, 20)

    removidos = recorrencias.encerrar(sessao, regra, HOJE)
    assert removidos == 3
    assert not regra.ativa
    assert recorrencias.gerar_pendentes(sessao, date(2027, 6, 30)) == 0


def test_limite_de_geracao():
    assert recorrencias.limite_geracao(Competencia(2026, 9), HOJE) == date(2026, 10, 31)
    assert recorrencias.limite_geracao(Competencia(2026, 12), HOJE) == date(2026, 12, 31)
    assert recorrencias.limite_geracao(Competencia(2030, 1), HOJE) == date(2027, 10, 31)


# ───────── Categorias (E2-04) ─────────


def test_subcategoria_herda_cor_e_so_um_nivel(sessao):
    moradia = _categoria(sessao, "Moradia")
    aluguel = categorias.salvar(
        sessao, {"nome": "Aluguel", "tipo": "saida", "pai_id": str(moradia.id), "cor_slot": "7"}
    )
    assert aluguel.cor_slot == moradia.cor_slot
    with pytest.raises(ErroValidacao) as erro:
        categorias.salvar(
            sessao, {"nome": "Neta", "tipo": "saida", "pai_id": str(aluguel.id), "cor_slot": "1"}
        )
    assert "pai_id" in erro.value.erros


def test_mudar_cor_do_pai_propaga(sessao):
    moradia = _categoria(sessao, "Moradia")
    categorias.salvar(
        sessao, {"nome": "Aluguel", "tipo": "saida", "pai_id": str(moradia.id), "cor_slot": "1"}
    )
    categorias.salvar(
        sessao, {"nome": "Moradia", "tipo": "saida", "cor_slot": "6", "icone": "house"}, moradia
    )
    assert {filha.cor_slot for filha in moradia.subcategorias} == {6}


def test_pai_de_outro_tipo_e_recusado(sessao):
    with pytest.raises(ErroValidacao):
        categorias.salvar(
            sessao,
            {"nome": "Bônus", "tipo": "entrada", "pai_id": str(_categoria(sessao, "Lazer").id),
             "cor_slot": "1"},
        )  # fmt: skip


def test_excluir_categoria_sem_uso_apaga_e_com_uso_arquiva(sessao, pessoal):
    nova = categorias.salvar(
        sessao, {"nome": "Pets", "tipo": "saida", "cor_slot": "2", "icone": "paw-print"}
    )
    assert categorias.excluir(sessao, nova) is not None
    assert _categoria(sessao, "Pets") is None

    lazer = _categoria(sessao, "Lazer")
    _lancar(sessao, pessoal, categoria_id=lazer.id)
    assert categorias.excluir(sessao, lazer) is None
    assert not lazer.ativa
    assert lazer.id not in dict(categorias.opcoes(sessao, TipoCategoria.SAIDA))
    assert lazer.id in dict(categorias.opcoes(sessao, TipoCategoria.SAIDA, manter_id=lazer.id))


def test_tipo_de_categoria_em_uso_nao_muda(sessao, pessoal):
    mercado = _categoria(sessao, "Mercado")
    _lancar(sessao, pessoal)
    with pytest.raises(ErroValidacao) as erro:
        categorias.salvar(
            sessao,
            {"nome": "Mercado", "tipo": "entrada", "cor_slot": "2", "icone": "shopping-cart"},
            mercado,
        )  # fmt: skip
    assert "tipo" in erro.value.erros


def test_somar_meses():
    assert somar_meses(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert somar_meses(date(2026, 11, 15), 3) == date(2027, 2, 15)
    assert somar_meses(date(2026, 3, 31), -1) == date(2026, 2, 28)

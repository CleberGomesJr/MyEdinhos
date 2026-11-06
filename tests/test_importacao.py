"""Importar extrato (E2-13), operações (E4-11) e leitura de planilhas."""

import base64
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.db.models import Ativo, Categoria, Conta, Lancamento, Operacao, TipoCategoria
from app.domain import ativos, carteira, cartoes, importacao, importacao_operacoes, operacoes
from app.domain.planilhas import ErroArquivo, decodificar, ler_data, ler_numero, ler_tabela

CSV_CONTA = """Banco Exemplo - Extrato
Período: outubro/2026

Data;Histórico;Valor;Saldo
01/10/2026;Salário Empresa;5.000,00;5.000,00
03/10/2026;Mercado Bom Preço;-234,56;4.765,44
03/10/2026;Mercado Bom Preço;-234,56;4.530,88
05/10/2026;Pagamento de fatura cartão;-1.000,00;3.530,88
31/10/2026;Saldo do dia;0,00;3.530,88
""".encode("cp1252")

CSV_NUBANK = b"""date,title,amount
2026-10-02,Padaria,12.50
2026-10-04,Pagamento recebido,-100.00
"""

OFX = b"""OFXHEADER:100
DATA:OFXSGML

<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKTRANLIST>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20261002120000[-3:BRT]
<TRNAMT>-45.90
<FITID>ABC123
<MEMO>Farmacia
</STMTTRN>
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20261005
<TRNAMT>100,00
<FITID>ABC124
<NAME>Pix recebido
</STMTTRN>
</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>
"""

CSV_B3 = """Data do Negócio;Tipo de Movimentação;Mercado;Prazo/Vencimento;Instituição;Código de Negociação;Quantidade;Preço;Valor
05/10/2026;Venda;Mercado à Vista;-;CORRETORA;ITSA4;40;R$ 11,00;R$ 440,00
02/10/2026;Compra;Mercado Fracionário;-;CORRETORA;ITSA4F;50;R$ 10,00;R$ 500,00
02/10/2026;Compra;Mercado à Vista;-;CORRETORA;HGLG11;3;160,50;481,50
""".encode()  # noqa: E501

CSV_MOVIMENTACAO = """Entrada/Saída;Data;Movimentação;Produto;Instituição;Quantidade;Preço unitário;Valor da Operação
Credito;15/10/2026;Dividendo;ITSA4 - ITAUSA S/A;CORRETORA;50;0,20;10,00
Credito;16/10/2026;Juros Sobre Capital Próprio;ITSA4 - ITAUSA S/A;CORRETORA;50;0,10;5,00
Credito;02/10/2026;Transferência - Liquidação;ITSA4 - ITAUSA S/A;CORRETORA;50;-;-
Credito;20/10/2026;Desdobro;ITSA4 - ITAUSA S/A;CORRETORA;50;-;-
Credito;02/10/2026;Compra;Tesouro Selic 2029;CORRETORA;1;20000,00;20000,00
""".encode()  # noqa: E501


@pytest.fixture
def conta(sessao):
    conta = sessao.scalars(select(Conta)).first()
    conta.data_saldo_inicial = date(2026, 1, 1)
    sessao.commit()  # as telas usam outra conexão
    return conta


@pytest.fixture
def origem(conta):
    return importacao.Origem(conta=conta)


def _quantos(sessao, modelo):
    return sessao.scalar(select(func.count()).select_from(modelo))


def _marcar_tudo(itens):
    escolhas = {}
    for item in itens:
        escolhas[f"importar-{item.indice}"] = "1"
        escolhas[f"categoria-{item.indice}"] = str(item.categoria_id)
    return escolhas


# ───────── Planilhas ─────────


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("1.234,56", "1234.56"),
        ("1234.56", "1234.56"),
        ("1,234.56", "1234.56"),
        ("-123,45", "-123.45"),
        ("R$ 10,00", "10.00"),
        ("R$ -10,00", "-10.00"),
        ("(123,45)", "-123.45"),
        ("10,00-", "-10.00"),
        ("10,00 D", "-10.00"),
        ("10,00 C", "10.00"),
        ("+5", "5"),
        ("1.500", "1500"),
        ("0.001", "0.001"),
        ("1,500", "1500"),
        ("12.50", "12.50"),
    ],
)
def test_ler_numero(texto, esperado):
    assert ler_numero(texto) == Decimal(esperado)


@pytest.mark.parametrize("texto", ["", "abc", "1,2,3", "--", "D"])
def test_ler_numero_recusa_o_que_nao_e_numero(texto):
    with pytest.raises(ValueError):
        ler_numero(texto)


@pytest.mark.parametrize(
    "texto", ["01/10/2026", "2026-10-01", "01-10-2026", "01.10.2026", "01/10/26", "20261001",
              "01/10/2026 12:33", "2026-10-01T12:33:00"],
)  # fmt: skip
def test_ler_data(texto):
    assert ler_data(texto) == date(2026, 10, 1)


def test_tabela_ignora_preambulo_e_acha_o_separador():
    tabela = ler_tabela(decodificar(CSV_CONTA))
    assert tabela.cabecalho == ["Data", "Histórico", "Valor", "Saldo"]
    assert len(tabela.linhas) == 5
    assert tabela.numero_inicial == 5


def test_xlsx_e_recusado_com_instrucao():
    with pytest.raises(ErroArquivo, match="CSV"):
        decodificar(b"PK\x03\x04conteudo")


def test_arquivo_sem_dados_e_recusado():
    with pytest.raises(ErroArquivo):
        ler_tabela("Data;Valor\n")
    with pytest.raises(ErroArquivo):
        ler_tabela("só um texto qualquer")


# ───────── Extrato ─────────


def test_csv_le_valores_e_ignora_saldo(origem):
    transacoes, erros = importacao.ler_arquivo(CSV_CONTA, origem)
    assert not erros
    assert [t.valor for t in transacoes] == [500000, -23456, -23456, -100000]
    assert transacoes[0].data == date(2026, 10, 1)
    assert transacoes[0].descricao == "Salário Empresa"
    # Duas compras iguais no mesmo dia são linhas diferentes.
    assert transacoes[1].id_externo != transacoes[2].id_externo


def test_csv_linha_ruim_vira_erro_com_o_numero(origem):
    arquivo = b"Data;Descricao;Valor\n01/10/2026;Ok;10,00\n99/99/2026;Ruim;10,00\n"
    transacoes, erros = importacao.ler_arquivo(arquivo, origem)
    assert len(transacoes) == 1
    assert erros == {3: "Data ou valor que não entendi."}


def test_colunas_de_credito_e_debito_separadas(origem):
    arquivo = "Data;Descrição;Crédito;Débito\n01/10/2026;Pix;50,00;\n02/10/2026;Luz;;80,00\n"
    tabela = ler_tabela(arquivo)
    mapa = importacao.adivinhar_mapeamento(tabela, origem)
    assert (mapa.credito, mapa.debito, mapa.valor) == ("Crédito", "Débito", "")
    transacoes, _ = importacao.ler_csv(tabela, mapa, origem.chave)
    assert [t.valor for t in transacoes] == [5000, -8000]


def test_fatura_de_cartao_positivo_e_saida(sessao, conta):
    cartao = cartoes.salvar(sessao, {
        "nome": "Roxo", "limite": "1000,00", "dia_fechamento": "10", "dia_vencimento": "17",
        "compra_no_fechamento": "fatura_atual", "conta_pagamento_id": str(conta.id),
    })  # fmt: skip
    origem = importacao.Origem(cartao=cartao)
    transacoes, _ = importacao.ler_arquivo(CSV_NUBANK, origem)
    assert [t.valor for t in transacoes] == [-1250, 10000]
    itens = importacao.revisar(sessao, transacoes, origem)
    assert itens[0].situacao == "nova"
    assert itens[1].situacao == "fatura"
    assert not itens[1].importar


def test_ofx_sgml(origem):
    transacoes, erros = importacao.ler_arquivo(OFX, origem)
    assert not erros
    assert [(t.data, t.valor, t.descricao, t.id_externo) for t in transacoes] == [
        (date(2026, 10, 2), -4590, "Farmacia", "ofx:ABC123"),
        (date(2026, 10, 5), 10000, "Pix recebido", "ofx:ABC124"),
    ]


def test_importar_e_reimportar_nao_duplica(sessao, origem, conta):
    transacoes, _ = importacao.ler_arquivo(CSV_CONTA, origem)
    itens = importacao.revisar(sessao, transacoes, origem)
    assert [i.situacao for i in itens] == ["nova", "nova", "nova", "fatura"]
    assert [i.importar for i in itens] == [True, True, True, False]

    escolhas = {k: v for k, v in _marcar_tudo(itens).items() if not k.endswith("-3")}
    antes = _quantos(sessao, Lancamento)
    criados = importacao.confirmar(sessao, transacoes, origem, escolhas)
    assert len(criados) == 3
    assert _quantos(sessao, Lancamento) == antes + 3
    assert all(c.pago and c.conta_id == conta.id for c in criados)
    assert criados[0].tipo.value == "entrada"
    assert criados[1].valor_centavos == 23456

    # O mesmo arquivo de novo: tudo já importado, e marcar à força também não duplica.
    itens = importacao.revisar(sessao, transacoes, origem)
    assert [i.situacao for i in itens] == ["importada", "importada", "importada", "fatura"]
    assert importacao.confirmar(sessao, transacoes[:3], origem, _marcar_tudo(itens)) == []
    assert _quantos(sessao, Lancamento) == antes + 3


def test_data_futura_no_extrato_fica_pendente(sessao, origem):
    arquivo = b"Data;Descricao;Valor\n10/10/2026;Passado;-1,00\n20/10/2026;Agendado;-1,00\n"
    transacoes, _ = importacao.ler_arquivo(arquivo, origem)
    itens = importacao.revisar(sessao, transacoes, origem)
    passado, agendado = importacao.confirmar(
        sessao, transacoes, origem, _marcar_tudo(itens), hoje=date(2026, 10, 15)
    )
    assert passado.pago
    assert not agendado.pago


def test_lancamento_a_mao_com_mesma_data_e_valor_e_possivel_duplicata(sessao, origem, conta):
    categoria = sessao.scalars(
        select(Categoria).where(Categoria.tipo == TipoCategoria.SAIDA)
    ).first()
    sessao.add(
        Lancamento(
            tipo="saida", valor_centavos=23456, descricao="Mercado", data=date(2026, 10, 3),
            competencia="2026-10", conta_id=conta.id, categoria_id=categoria.id, pago=True,
        )
    )  # fmt: skip
    sessao.flush()
    transacoes, _ = importacao.ler_arquivo(CSV_CONTA, origem)
    itens = importacao.revisar(sessao, transacoes, origem)
    # Só uma das duas compras iguais do arquivo bate com a lançada à mão.
    assert [i.situacao for i in itens[1:3]] == ["duplicata", "nova"]
    assert [i.importar for i in itens[1:3]] == [False, True]


def test_categoria_sugerida_pela_descricao(sessao, origem, conta):
    categoria = sessao.scalars(
        select(Categoria).where(
            Categoria.tipo == TipoCategoria.SAIDA, Categoria.nome != "Outros gastos"
        )
    ).first()
    sessao.add(
        Lancamento(
            tipo="saida", valor_centavos=999, descricao="MERCADO BOM PREÇO", data=date(2026, 9, 3),
            competencia="2026-09", conta_id=conta.id, categoria_id=categoria.id, pago=True,
        )
    )  # fmt: skip
    sessao.flush()
    transacoes, _ = importacao.ler_arquivo(CSV_CONTA, origem)
    itens = importacao.revisar(sessao, transacoes, origem)
    assert itens[1].categoria_id == categoria.id


def test_confirmar_e_tudo_ou_nada_e_desfazer_lote(sessao, origem):
    transacoes, _ = importacao.ler_arquivo(CSV_CONTA, origem)
    itens = importacao.revisar(sessao, transacoes, origem)
    criados = importacao.confirmar(sessao, transacoes[:3], origem, _marcar_tudo(itens[:3]))
    importacao.guardar_lote(sessao, importacao.LOTE_EXTRATO, criados)
    antes = _quantos(sessao, Lancamento)
    assert len(importacao.ler_lote(sessao, importacao.LOTE_EXTRATO, Lancamento)) == 3

    registro = importacao.desfazer_lote(sessao, importacao.LOTE_EXTRATO, Lancamento, "Lote {}")
    assert registro.descricao == "Lote 3"
    assert _quantos(sessao, Lancamento) == antes - 3
    assert importacao.ler_lote(sessao, importacao.LOTE_EXTRATO, Lancamento) == []
    assert importacao.desfazer_lote(sessao, importacao.LOTE_EXTRATO, Lancamento, "{}") is None


def _enviar(cliente, url, conteudo, nome="extrato.csv", **dados):
    return cliente.post(url, data=dados, files={"arquivo": (nome, conteudo)})


def test_tela_extrato_do_arquivo_ate_o_fim(cliente, sessao, conta):
    chave = f"conta-{conta.id}"
    resposta = cliente.get("/lancamentos/importar")
    assert resposta.status_code == 200
    assert "Importar extrato" in resposta.text

    resposta = _enviar(cliente, "/lancamentos/importar", CSV_CONTA, origem=chave)
    assert resposta.status_code == 200
    assert "O que é cada coluna?" in resposta.text
    conteudo = base64.b64encode(CSV_CONTA).decode()

    corpo = {
        "origem": chave, "conteudo": conteudo, "col_data": "Data", "col_descricao": "Histórico",
        "col_valor": "Valor", "sinal": "negativo",
    }  # fmt: skip
    resposta = cliente.post("/lancamentos/importar/revisar", data=corpo)
    assert resposta.status_code == 200
    assert "Mercado Bom Preço" in resposta.text
    assert "4 linha(s)" in resposta.text

    categoria = sessao.scalars(
        select(Categoria).where(Categoria.tipo == TipoCategoria.SAIDA)
    ).first()
    entrada = sessao.scalars(
        select(Categoria).where(Categoria.tipo == TipoCategoria.ENTRADA)
    ).first()
    marcas = {
        "importar-0": "1",
        "categoria-0": entrada.id,
        "importar-1": "1",
        "categoria-1": categoria.id,
    }
    resposta = cliente.post("/lancamentos/importar/confirmar", data=corpo | marcas)
    assert resposta.status_code == 200
    assert "2 lançamento(s) importado(s)" in resposta.text
    assert "A última importação criou 2" in cliente.get("/lancamentos/importar").text

    resposta = cliente.post("/lancamentos/importar/desfazer")
    assert resposta.status_code == 204
    assert "A última importação" not in cliente.get("/lancamentos/importar").text


def test_tela_extrato_ofx_vai_direto_para_a_revisao(cliente, conta):
    resposta = _enviar(
        cliente, "/lancamentos/importar", OFX, "extrato.ofx", origem=f"conta-{conta.id}"
    )
    assert resposta.status_code == 200
    assert "Confira antes de importar" in resposta.text
    assert "Pix recebido" in resposta.text


def test_tela_extrato_erros(cliente, conta):
    chave = f"conta-{conta.id}"
    assert (
        _enviar(cliente, "/lancamentos/importar", CSV_CONTA, origem="conta-999").status_code == 422
    )
    resposta = _enviar(cliente, "/lancamentos/importar", b"PK\x03\x04x", "a.xlsx", origem=chave)
    assert resposta.status_code == 422
    assert "Salvar como" in resposta.text
    resposta = cliente.post("/lancamentos/importar", data={"origem": chave})
    assert resposta.status_code == 422
    corpo = {"origem": chave, "conteudo": base64.b64encode(CSV_CONTA).decode(), "col_data": "Data"}
    resposta = cliente.post("/lancamentos/importar/revisar", data=corpo)
    assert resposta.status_code == 422
    assert "colunas de data e de descrição" in resposta.text
    resposta = cliente.post("/lancamentos/importar/revisar", data=corpo | {"conteudo": "@@@"})
    assert resposta.status_code == 422


def test_tela_conteudo_escapado(cliente, conta):
    arquivo = "Data;Descrição;Valor\n01/10/2026;<script>alert(1)</script>;-1,00\n".encode()
    corpo = {"origem": f"conta-{conta.id}", "conteudo": base64.b64encode(arquivo).decode(),
             "col_data": "Data", "col_descricao": "Descrição", "col_valor": "Valor"}  # fmt: skip
    resposta = cliente.post("/lancamentos/importar/revisar", data=corpo)
    assert "<script>alert(1)" not in resposta.text
    assert "&lt;script&gt;alert(1)" in resposta.text


# ───────── Operações ─────────


def _linhas(conteudo: bytes):
    tabela = ler_tabela(decodificar(conteudo))
    return importacao_operacoes.ler_linhas(
        tabela, importacao_operacoes.adivinhar_mapeamento(tabela)
    )


def _tudo(linhas):
    return {f"importar-{ln.numero}": "1" for ln in linhas}


def test_le_negociacao_da_b3():
    linhas = _linhas(CSV_B3)
    assert [ln.ticker for ln in linhas] == ["ITSA4", "ITSA4", "HGLG11"]  # sem o F do fracionário
    assert linhas[0].tipo.value == "venda"
    assert linhas[0].preco == Decimal("11.00")
    assert linhas[2].quantidade == 3


def test_le_movimentacao_da_b3():
    linhas = _linhas(CSV_MOVIMENTACAO)
    assert [(ln.tipo.value if ln.tipo else None, ln.situacao) for ln in linhas] == [
        ("dividendo", "nova"),
        ("jcp", "nova"),
        (None, "ignorada"),
        (None, "ignorada"),
        ("compra", "ignorada"),  # Tesouro: renda fixa fica à mão
    ]
    assert linhas[0].ticker == "ITSA4"
    assert linhas[0].valor == 1000
    assert "desdobramento" in linhas[3].motivo
    assert "Renda fixa" in linhas[4].motivo


def test_revisao_simula_sem_gravar_nada(sessao):
    sessao.commit()
    linhas = importacao_operacoes.revisar(sessao, _linhas(CSV_B3))
    assert [ln.situacao for ln in linhas] == ["nova", "nova", "nova"]
    assert all(ln.ativo_novo for ln in linhas)
    # Nem operação, nem ativo: o RELEASE do savepoint não pode gravar de vez.
    sessao.rollback()
    assert _quantos(sessao, Operacao) == 0
    assert _quantos(sessao, Ativo) == 0


def test_venda_sem_posicao_aparece_na_revisao(sessao):
    sessao.commit()
    arquivo = "Data;Tipo;Ativo;Quantidade;Preço\n01/10/2026;Venda;PETR4;10;30,00\n".encode()
    (linha,) = importacao_operacoes.revisar(sessao, _linhas(arquivo))
    assert linha.situacao == "erro"
    assert "PETR4" in linha.motivo


def test_importa_em_ordem_de_data_e_nao_duplica(sessao):
    sessao.commit()
    linhas = _linhas(CSV_B3)
    criadas, falhas = importacao_operacoes.confirmar(sessao, linhas, _tudo(linhas))
    sessao.commit()
    assert len(criadas) == 3
    assert not falhas
    itsa = ativos.por_ticker(sessao, "ITSA4")
    assert itsa.classe.value == "acao"
    assert ativos.por_ticker(sessao, "HGLG11").classe.value == "fii"
    posicao = carteira.posicoes(sessao, date(2026, 10, 31))[itsa.id]
    assert posicao.quantidade == 10  # comprou 50 e vendeu 40, mesmo a venda vindo antes no arquivo

    de_novo = _linhas(CSV_B3)
    criadas, falhas = importacao_operacoes.confirmar(sessao, de_novo, _tudo(de_novo))
    assert criadas == []
    assert [ln.situacao for ln in de_novo] == ["importada"] * 3


def test_linha_com_erro_nao_atrapalha_as_outras(sessao):
    sessao.commit()
    arquivo = (
        "Data;Tipo;Ativo;Quantidade;Preço\n"
        "01/10/2026;Venda;PETR4;10;30,00\n"
        "02/10/2026;Compra;VALE3;5;60,00\n"
    ).encode()
    linhas = _linhas(arquivo)
    criadas, falhas = importacao_operacoes.confirmar(sessao, linhas, _tudo(linhas))
    sessao.commit()
    assert len(criadas) == 1
    assert [ln.ticker for ln in falhas] == ["PETR4"]
    assert _quantos(sessao, Operacao) == 1


def test_operacao_a_mao_igual_e_possivel_duplicata(sessao):
    ativo = ativos.salvar(sessao, {"ticker": "HGLG11", "nome": "HGLG", "classe": "fii"})
    operacoes.salvar(sessao, {
        "ativo_id": str(ativo.id), "tipo": "compra", "data": "2026-10-02", "quantidade": "3",
        "preco_unitario": "160,50",
    })  # fmt: skip
    sessao.commit()
    linhas = importacao_operacoes.revisar(sessao, _linhas(CSV_B3))
    assert linhas[2].situacao == "duplicata"
    assert linhas[2].importavel


def test_conta_opcional_move_saldo(sessao, conta):
    sessao.commit()
    arquivo = "Data;Tipo;Ativo;Quantidade;Preço\n02/10/2026;Compra;VALE3;5;60,00\n".encode()
    linhas = _linhas(arquivo)
    (criada,), _ = importacao_operacoes.confirmar(sessao, linhas, _tudo(linhas), str(conta.id))
    assert criada.conta_id == conta.id


def test_so_o_total_calcula_o_preco(sessao):
    sessao.commit()
    arquivo = b"Data;Tipo;Ativo;Quantidade;Valor\n02/10/2026;Compra;VALE3;3;100,00\n"
    linhas = _linhas(arquivo)
    (criada,), falhas = importacao_operacoes.confirmar(sessao, linhas, _tudo(linhas))
    assert not falhas
    assert criada.valor_total_centavos == 10000


def test_tela_operacoes_do_arquivo_ate_o_fim(cliente, sessao):
    assert cliente.get("/carteira/importar").status_code == 200
    resposta = _enviar(cliente, "/carteira/importar", CSV_B3)
    assert resposta.status_code == 200
    assert "O que é cada coluna?" in resposta.text
    corpo = {
        "conteudo": base64.b64encode(CSV_B3).decode(), "conta_id": "",
        "col_data": "Data do Negócio", "col_ticker": "Código de Negociação",
        "col_tipo": "Tipo de Movimentação", "col_quantidade": "Quantidade", "col_preco": "Preço",
        "col_valor": "Valor", "col_taxas": "",
    }  # fmt: skip
    resposta = cliente.post("/carteira/importar/revisar", data=corpo)
    assert resposta.status_code == 200
    assert "Ativo novo" in resposta.text
    assert _quantos(sessao, Operacao) == 0

    marcas = {"importar-2": "1", "importar-3": "1", "importar-4": "1"}
    resposta = cliente.post("/carteira/importar/confirmar", data=corpo | marcas)
    assert resposta.status_code == 200
    assert "3 operação(ões) importada(s)" in resposta.text
    assert "A última importação criou 3" in cliente.get("/carteira/importar").text
    assert cliente.post("/carteira/importar/desfazer").status_code == 204
    assert _quantos(sessao, Operacao) == 0


def test_tela_operacoes_erros(cliente):
    assert _enviar(cliente, "/carteira/importar", b"").status_code == 422
    corpo = {"conteudo": base64.b64encode(CSV_B3).decode(), "col_data": "Data do Negócio"}
    resposta = cliente.post("/carteira/importar/revisar", data=corpo)
    assert resposta.status_code == 422
    assert "data, ativo e tipo" in resposta.text

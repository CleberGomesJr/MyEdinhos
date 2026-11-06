"""Dados de demonstração (E7-01): 12 meses fictícios para ver o app sem expor dados reais.

Tudo vai para `<pasta de dados>/demo/`, nunca para o banco real. Os códigos de ativos são
inventados e as cotações, simuladas: o app em modo demonstração não busca dados de mercado.
Sequência fixa de números aleatórios: gerar de novo dá os mesmos dados.
"""

import random
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.migracoes import aplicar_migracoes
from app.db.models import (
    Ativo,
    Categoria,
    Conta,
    Cotacao,
    IndiceMacro,
    ProventoAnunciado,
    TipoProvento,
    TituloTesouro,
)
from app.db.session import criar_engine, criar_fabrica_sessao
from app.domain import (
    alocacao,
    assistente,
    ativos,
    cartoes,
    contas,
    lancamentos,
    mercado,
    objetivos,
    operacoes,
    orcamentos,
    proventos,
    regras,
)
from app.domain.competencia import Competencia
from app.domain.datas import hoje
from app.domain.dinheiro import formatar_brl
from app.domain.resumos import meses_ate

SEMENTE = 2026
ARQUIVOS_DO_BANCO = ("financas.db", "financas.db-wal", "financas.db-shm")

CARTEIRA = (  # código, nome, classe, setor, quantidade, preço inicial, provento por unidade
    ("BANC4", "Banco Exemplo", "acao", "Bancos", 60, "24.00", "0.32"),
    ("ENER3", "Energia Exemplo", "acao", "Energia", 40, "31.00", "0.55"),
    ("SANE3", "Saneamento Exemplo", "acao", "Saneamento", 25, "48.00", "0.70"),
    ("LOGI11", "FII Logística Exemplo", "fii", "Logística", 12, "98.00", "0.78"),
    ("PAPR11", "FII Papel Exemplo", "fii", "Papel (CRI)", 90, "9.60", "0.09"),
    ("MUND11", "ETF Mundo Exemplo", "etf", "Exterior", 15, "62.00", None),
)


def _valor(centavos: int) -> str:
    return formatar_brl(centavos).replace("R$ ", "")


def _no_mes(mes: Competencia, dia: int) -> date:
    return date(mes.ano, mes.mes, min(dia, mes.ultimo_dia.day))


def _financas(sessao: Session, sorteio: random.Random, meses: list[Competencia], dia: date) -> None:
    inicio = meses[0].primeiro_dia
    conta = sessao.scalar(select(Conta))  # banco novo vem sem conta: a demonstração cria a sua
    conta = contas.salvar(
        sessao,
        {
            "nome": conta.nome if conta else "Conta corrente",
            "tipo": "corrente",
            "saldo_inicial": "3.200,00",
            "data_saldo_inicial": inicio.isoformat(),
        },
        conta,
    )
    contas.salvar(
        sessao,
        {
            "nome": "Reserva",
            "tipo": "poupanca",
            "saldo_inicial": "9.000,00",
            "data_saldo_inicial": inicio.isoformat(),
            "reserva_emergencia": "1",
        },
    )
    cartao = cartoes.salvar(
        sessao,
        {
            "nome": "Cartão Exemplo",
            "limite": "8.000,00",
            "dia_fechamento": "19",
            "dia_vencimento": "26",
            "compra_no_fechamento": "fatura_atual",
            "conta_pagamento_id": str(conta.id),
        },
    )
    categoria = {c.nome: c.id for c in sessao.scalars(select(Categoria))}

    def lancar(tipo, centavos, data, nome, descricao, no_cartao=False):
        if data > dia:
            return
        dados = {
            "tipo": tipo,
            "valor": _valor(centavos),
            "descricao": descricao,
            "data": data.isoformat(),
            "categoria_id": str(categoria[nome]),
        }
        if no_cartao:
            dados |= {"meio": "cartao", "cartao_id": str(cartao.id)}
        else:
            dados |= {"conta_id": str(conta.id), "pago": "1"}
        lancamentos.salvar(sessao, dados)

    for mes in meses:
        lancar("entrada", 650000, _no_mes(mes, 5), "Salário", "Salário")
        if sorteio.random() < 0.3:
            lancar(
                "entrada",
                sorteio.randint(30000, 120000),
                _no_mes(mes, 17),
                "Renda extra",
                "Trabalho extra",
            )
        lancar("saida", 180000, _no_mes(mes, 10), "Moradia", "Aluguel")
        lancar("saida", sorteio.randint(14000, 26000), _no_mes(mes, 12), "Moradia", "Conta de luz")
        for dia_compra in (3, 11, 18, 25):
            lancar(
                "saida",
                sorteio.randint(18000, 42000),
                _no_mes(mes, dia_compra),
                "Mercado",
                "Supermercado",
                True,
            )
        lancar("saida", 5590, _no_mes(mes, 8), "Assinaturas", "Streaming", True)
        lancar(
            "saida", sorteio.randint(15000, 38000), _no_mes(mes, 14), "Transporte", "Combustível"
        )
        lancar(
            "saida", sorteio.randint(9000, 32000), _no_mes(mes, 20), "Restaurantes", "Jantar", True
        )
        lancar("saida", sorteio.randint(6000, 26000), _no_mes(mes, 22), "Lazer", "Passeio")
        if sorteio.random() < 0.4:
            lancar("saida", sorteio.randint(12000, 30000), _no_mes(mes, 16), "Saúde", "Consulta")
        fatura = cartoes.obter_fatura(sessao, cartao, mes)
        restante = cartoes.resumo(sessao, fatura, dia).restante
        if restante and fatura.data_vencimento <= dia:
            cartoes.pagar(
                sessao,
                fatura,
                {
                    "valor": _valor(restante),
                    "data": fatura.data_vencimento.isoformat(),
                    "conta_id": str(conta.id),
                },
            )
    # Um a pagar, para o painel "A pagar" ter o que mostrar.
    lancamentos.salvar(
        sessao,
        {
            "tipo": "saida",
            "conta_id": str(conta.id),
            "valor": "120,00",
            "descricao": "Internet",
            "data": (dia + timedelta(days=3)).isoformat(),
            "categoria_id": str(categoria["Moradia"]),
        },
    )
    for nome, limite in (("Mercado", "1.400,00"), ("Lazer", "150,00"), ("Restaurantes", "300,00")):
        orcamentos.definir(
            sessao, sessao.get(Categoria, categoria[nome]), meses[0], {"limite": limite}
        )


def _cotacoes(sessao, sorteio, ativo: Ativo, preco: Decimal, inicio: date, dia: date) -> None:
    atual = inicio
    while atual <= dia:
        if atual.weekday() < 5:
            preco = (preco * (1 + Decimal(sorteio.randint(-150, 170)) / 10000)).quantize(
                Decimal("0.01")
            )
            sessao.add(
                Cotacao(ativo_id=ativo.id, data=atual, fechamento=preco, fonte="demonstração")
            )
        atual += timedelta(days=1)


def _investimentos(sessao, sorteio, meses: list[Competencia], dia: date) -> None:
    inicio = meses[0].primeiro_dia
    for ticker, nome, classe, setor, quantidade, preco, provento in CARTEIRA:
        ativo = ativos.salvar(
            sessao, {"ticker": ticker, "nome": nome, "classe": classe, "setor": setor}
        )
        operacoes.salvar(
            sessao,
            {
                "ativo_id": str(ativo.id),
                "tipo": "posicao_inicial",
                "data": inicio.isoformat(),
                "quantidade": str(quantidade),
                "preco_unitario": preco.replace(".", ","),
            },
        )
        _cotacoes(sessao, sorteio, ativo, Decimal(preco), inicio, dia)
        if provento is None:
            continue
        tipo = TipoProvento.RENDIMENTO if classe == "fii" else TipoProvento.DIVIDENDO
        pagamentos = meses if classe == "fii" else meses[2::3]
        for mes in pagamentos:
            data_com = _no_mes(mes, 15)
            pagamento = _no_mes(mes, 25)
            if data_com >= dia:
                continue
            anuncio = ProventoAnunciado(
                ativo_id=ativo.id,
                tipo=tipo,
                data_com=data_com,
                data_pagamento=pagamento,
                valor_por_unidade=Decimal(provento),
                fonte="demonstração",
            )
            sessao.add(anuncio)
            if pagamento < dia:
                recebido = int(Decimal(provento) * quantidade * 100)
                salva = operacoes.salvar(
                    sessao,
                    {
                        "ativo_id": str(ativo.id),
                        "tipo": tipo.value,
                        "data": pagamento.isoformat(),
                        "valor": _valor(recebido),
                    },
                )
                anuncio.operacao_id = salva.id

    tesouro = ativos.salvar(
        sessao,
        {
            "ticker": "SELIC-DEMO",
            "nome": "Tesouro Selic (exemplo)",
            "classe": "tesouro",
            "indexador": "selic",
            "reserva_emergencia": "1",
        },
    )
    operacoes.salvar(
        sessao,
        {
            "ativo_id": str(tesouro.id),
            "tipo": "compra",
            "data": inicio.isoformat(),
            "quantidade": "1",
            "preco_unitario": "5.000,00",
        },
    )

    atual, ibovespa = inicio, Decimal(125000)
    while atual <= dia:
        if atual.weekday() < 5:
            ibovespa = (ibovespa * (1 + Decimal(sorteio.randint(-120, 130)) / 10000)).quantize(
                Decimal("0.01")
            )
            for serie, valor in (
                ("cdi", Decimal("0.0507")),
                ("selic", Decimal("0.0507")),
                ("selic_meta", Decimal("13.75")),
                ("ibovespa", ibovespa),
            ):
                sessao.add(IndiceMacro(serie=serie, data=atual, valor=valor, fonte="demonstração"))
        atual += timedelta(days=1)
    for mes in meses:
        if mes.primeiro_dia <= dia:
            valor = Decimal(sorteio.randint(10, 60)) / 100
            sessao.add(
                IndiceMacro(serie="ipca", data=mes.primeiro_dia, valor=valor, fonte="demonstração")
            )


def _tesouro(sessao: Session, dia: date) -> None:
    for tipo, ano, taxa, preco in (
        ("Tesouro Selic", 2031, "0.08", "18250.40"),
        ("Tesouro IPCA+", 2035, "6.42", "2710.15"),
        ("Tesouro Prefixado", 2032, "12.85", "562.30"),
    ):
        sessao.add(
            TituloTesouro(
                tipo=tipo, vencimento=date(ano, 1, 1), data_base=dia, taxa_compra=Decimal(taxa),
                taxa_venda=Decimal(taxa), pu_compra=Decimal(preco), pu_venda=Decimal(preco),
            )
        )  # fmt: skip


def _analise(sessao: Session) -> None:
    metas = {f"alvo-{g.value}": str(a) for g, (a, _) in alocacao.SUGESTAO_MODERADO.items()}
    metas |= {f"tolerancia-{g.value}": str(t) for g, (_, t) in alocacao.SUGESTAO_MODERADO.items()}
    alocacao.salvar_metas(sessao, metas)
    regras.gravar(sessao, regras.SUGESTAO_MODERADO)
    objetivos.salvar_premissas(
        sessao,
        {
            "premissa_retorno_aa": "10",
            "premissa_yield": "7",
            "aporte_mensal": "400,00",
            "reserva_meses": "6",
        },
    )
    proventos.definir_meta(sessao, 50000)
    objetivos.salvar(sessao, {"nome": "Trocar de carro", "valor_alvo": "40.000,00"})
    mercado.definir_automatico(sessao, False)
    assistente.encerrar(sessao)


def pasta_demo(settings: Settings) -> Path:
    return settings.data_dir / "demo"


def settings_demo(settings: Settings) -> Settings:
    """Mesma configuração, com os dados na pasta de demonstração e sem token de mercado."""
    return Settings(
        data_dir=pasta_demo(settings),
        porta=settings.porta + 1,
        nivel_log=settings.nivel_log,
        modo_demo=True,
        brapi_token=None,
    )


def gerar(settings: Settings, dia: date | None = None) -> Settings:
    """Recria o banco de demonstração. Só apaga arquivos dentro da pasta `demo`."""
    demo = settings_demo(settings)
    demo.garantir_pastas()
    for nome in ARQUIVOS_DO_BANCO:
        (demo.data_dir / nome).unlink(missing_ok=True)
    aplicar_migracoes(demo.database_url)
    dia = dia or hoje()
    meses = meses_ate(Competencia.de_data(dia), 12)
    sorteio = random.Random(SEMENTE)
    engine = criar_engine(demo.database_url)
    try:
        with criar_fabrica_sessao(engine)() as sessao:
            _financas(sessao, sorteio, meses, dia)
            _investimentos(sessao, sorteio, meses, dia)
            _tesouro(sessao, dia)
            _analise(sessao)
            sessao.commit()
    finally:
        engine.dispose()
    return demo

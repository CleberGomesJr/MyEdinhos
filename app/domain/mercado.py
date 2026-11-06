"""Atualização de cotações, proventos anunciados e índices (E5-06, E5-07, E5-09, E5-10).

Incremental: busca só a partir da última data guardada. Cadeia de fontes: brapi (se houver
token) → Yahoo. Falha de rede não apaga nada: o app segue com o último dado guardado.
"""

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.base import agora_utc
from app.db.models import (
    Ativo,
    ClasseAtivo,
    Cotacao,
    Indexador,
    IndiceMacro,
    Operacao,
    ProventoAnunciado,
    TipoProvento,
    TituloTesouro,
)
from app.domain import preferencias
from app.integrations.mercado import bcb, tesouro
from app.integrations.mercado.base import DadosAtivo, ErroFonte, FonteCotacoes
from app.integrations.mercado.brapi import Brapi
from app.integrations.mercado.yahoo import Yahoo

log = logging.getLogger(__name__)

# Resposta fora do formato também é falha da fonte: Yahoo e brapi podem mudar sem aviso, e
# um ativo com problema não pode derrubar a atualização dos outros.
ERROS_DE_FONTE = (
    ErroFonte, KeyError, IndexError, TypeError, ValueError, AttributeError, ArithmeticError,
)  # fmt: skip
STATUS = "mercado_status"
TESOURO_BUSCADO = "tesouro_buscado_em"
AUTOMATICO = "mercado_atualizar_ao_abrir"  # "sim" (padrão) ou "nao"
HISTORICO_INICIAL = 400  # dias na primeira busca (DY de 12 meses precisa de um ano de proventos)
LIMITE_BRAPI = 15_000  # requisições/mês no plano gratuito
ALERTA_BRAPI = 0.8


def chave_requisicoes(hoje: date) -> str:
    return f"brapi_requisicoes_{hoje:%Y-%m}"


def contar_requisicao(sessao: Session, hoje: date) -> None:
    chave = chave_requisicoes(hoje)
    preferencias.definir(sessao, chave, str(int(preferencias.obter(sessao, chave) or 0) + 1))
    sessao.commit()  # chamado logo antes da rede: não segura o banco durante a requisição


def requisicoes_brapi(sessao: Session, hoje: date) -> int:
    return int(preferencias.obter(sessao, chave_requisicoes(hoje)) or 0)


@dataclass
class Relatorio:
    em: datetime
    atualizados: list[str] = field(default_factory=list)
    erros: dict[str, str] = field(default_factory=dict)
    cotacoes: int = 0
    proventos: int = 0
    indices: int = 0
    fontes: dict[str, str] = field(default_factory=dict)  # ticker → fonte usada

    def para_json(self) -> str:
        return json.dumps(
            {
                "em": self.em.isoformat(),
                "atualizados": self.atualizados,
                "erros": self.erros,
                "cotacoes": self.cotacoes,
                "proventos": self.proventos,
                "indices": self.indices,
                "fontes": self.fontes,
            }  # fmt: skip
        )


def fontes_configuradas(sessao: Session, token_brapi: str | None, hoje: date) -> list:
    """brapi primeiro quando há token (e a cota do mês não acabou); Yahoo sempre como reserva."""
    fontes: list = []
    if token_brapi and requisicoes_brapi(sessao, hoje) < LIMITE_BRAPI:
        fontes.append(Brapi(token_brapi, lambda: contar_requisicao(sessao, hoje)))
    fontes.append(Yahoo())
    return fontes


def ativos_para_atualizar(sessao: Session) -> list[Ativo]:
    return [a for a in sessao.scalars(select(Ativo).where(Ativo.ativo)) if a.classe.tem_cotacao]


def _desde(sessao: Session, ativo: Ativo, hoje: date) -> date:
    ultima = sessao.scalar(
        select(func.max(Cotacao.data)).where(
            Cotacao.ativo_id == ativo.id, Cotacao.fonte != "manual"
        )
    )
    return ultima if ultima else hoje - timedelta(days=HISTORICO_INICIAL)


def _gravar_cotacoes(sessao: Session, ativo: Ativo, dados, agora: datetime) -> int:
    existentes = {
        c.data: c
        for c in sessao.scalars(
            select(Cotacao).where(
                Cotacao.ativo_id == ativo.id,
                Cotacao.data.in_([f.data for f in dados.fechamentos]),
            )
        )
    }
    for fechamento in dados.fechamentos:
        cotacao = existentes.get(fechamento.data)
        if cotacao is None:
            sessao.add(Cotacao(ativo_id=ativo.id, data=fechamento.data, fechamento=fechamento.valor,
                               fonte=dados.fonte, obtido_em=agora))  # fmt: skip
        else:
            cotacao.fechamento, cotacao.fonte, cotacao.obtido_em = (
                fechamento.valor,
                dados.fonte,
                agora,
            )
    return len(dados.fechamentos)


def _gravar_proventos(sessao: Session, ativo: Ativo, dados, agora: datetime) -> int:
    novos = 0
    for provento in dados.proventos:
        tipo = TipoProvento(provento.tipo)
        existente = sessao.scalar(
            select(ProventoAnunciado).where(
                ProventoAnunciado.ativo_id == ativo.id,
                ProventoAnunciado.tipo == tipo,
                ProventoAnunciado.data_com == provento.data_com,
            )
        )
        if existente is None:
            existente = ProventoAnunciado(ativo_id=ativo.id, tipo=tipo, data_com=provento.data_com)
            sessao.add(existente)
            novos += 1
        # Fonte com data de pagamento (brapi) vale mais que a sem (Yahoo).
        if provento.data_pagamento or existente.data_pagamento is None:
            existente.data_pagamento = provento.data_pagamento
            existente.valor_por_unidade = provento.valor_por_unidade
            existente.fonte = dados.fonte
            existente.obtido_em = agora
    sessao.flush()
    return novos


def _historico_de_proventos(sessao, ativo: Ativo, fontes, hoje: date, agora: datetime) -> int:
    """Uma vez por ativo: proventos dos últimos 10 anos (E6-11), da fonte que tiver isso."""
    chave = f"proventos_historico_{ativo.id}"
    if preferencias.obter(sessao, chave):
        return 0
    for fonte in fontes:
        if hasattr(fonte, "proventos_historicos"):
            try:
                lista = fonte.proventos_historicos(
                    ativo.ticker, ativo.classe.value, hoje - timedelta(days=3653), hoje
                )
            except ERROS_DE_FONTE:
                return 0  # tenta de novo na próxima atualização
            preferencias.definir(sessao, chave, agora.date().isoformat())
            return _gravar_proventos(sessao, ativo, DadosAtivo(fonte.nome, [], lista), agora)
    return 0


def atualizar_ativo(
    sessao: Session, ativo: Ativo, fontes: Sequence[FonteCotacoes], hoje: date, agora: datetime,
    relatorio: Relatorio,
) -> None:  # fmt: skip
    desde = _desde(sessao, ativo, hoje)
    # E5-08: histórico maior que o limite da fonte principal (brapi grátis: 3 meses) vem
    # primeiro de uma fonte sem limite; depois a cadeia normal segue nas atualizações.
    dias = (hoje - desde).days
    ordem = sorted(fontes, key=lambda f: (getattr(f, "historico_dias", None) or 10**6) < dias)
    erros = []
    for fonte in ordem:
        try:
            dados = fonte.buscar(ativo.ticker, ativo.classe.value, desde, hoje)
        except ERROS_DE_FONTE as erro:
            erros.append(f"{fonte.nome}: {erro or type(erro).__name__}")
            continue
        relatorio.cotacoes += _gravar_cotacoes(sessao, ativo, dados, agora)
        relatorio.proventos += _gravar_proventos(sessao, ativo, dados, agora)
        sessao.commit()  # não segura o banco na próxima chamada de rede
        relatorio.proventos += _historico_de_proventos(sessao, ativo, fontes, hoje, agora)
        sessao.commit()
        relatorio.atualizados.append(ativo.ticker)
        relatorio.fontes[ativo.ticker] = dados.fonte
        return
    relatorio.erros[ativo.ticker] = "; ".join(erros) or "nenhuma fonte configurada"


def atualizar_indices(
    sessao: Session,
    hoje: date,
    buscar: Callable | None = None,
    relatorio: Relatorio | None = None,
) -> int:
    """Séries do Banco Central: o que falta depois da última data guardada e, se preciso,
    o começo (pelo menos 13 meses, ou desde a aplicação mais antiga; no máximo 5 anos)."""
    primeira = sessao.scalar(select(func.min(Operacao.data)))
    # Pelo menos 13 meses (acumulados de 12 meses), ou desde a aplicação mais antiga; máx. 5 anos.
    alvo = max(min(primeira or hoje, hoje - timedelta(days=400)), hoje - timedelta(days=5 * 365))
    buscar = buscar or bcb.buscar_serie  # resolvido na chamada: os testes trocam por um falso
    total = 0
    for serie in bcb.SERIES:
        guardadas = set(sessao.scalars(select(IndiceMacro.data).where(IndiceMacro.serie == serie)))
        periodos = []
        if not guardadas:
            periodos.append((alvo, hoje + timedelta(days=7)))
        else:
            if min(guardadas) > alvo + timedelta(days=31):  # completa o começo que falta
                periodos.append((alvo, min(guardadas) - timedelta(days=1)))
            periodos.append((max(guardadas) + timedelta(days=1), hoje + timedelta(days=7)))
        for inicio, fim in periodos:
            if inicio > fim:
                continue
            try:
                valores = buscar(serie, inicio, fim)
            except ERROS_DE_FONTE as erro:
                if relatorio is not None:
                    relatorio.erros[f"índice {serie}"] = str(erro)
                continue
            for dia, valor in valores:
                if inicio <= dia <= fim and dia not in guardadas:
                    sessao.add(IndiceMacro(serie=serie, data=dia, valor=valor, fonte="bcb"))
                    guardadas.add(dia)
                    total += 1
            sessao.commit()  # não segura o banco na próxima chamada de rede
    sessao.flush()
    return total


def tipo_do_titulo(ativo: Ativo) -> str | None:
    """Nome do título no arquivo do Tesouro, pelo indexador e pelo nome do ativo."""
    texto = f"{ativo.ticker} {ativo.nome}".lower()
    if "renda+" in texto or "renda mais" in texto:
        return "Tesouro Renda+ Aposentadoria Extra"
    if "educa" in texto:
        return "Tesouro Educa+"
    semestrais = "juros" in texto or "semestr" in texto
    if ativo.indexador == Indexador.SELIC:
        return "Tesouro Selic"
    if ativo.indexador == Indexador.IPCA:
        return "Tesouro IPCA+ com Juros Semestrais" if semestrais else "Tesouro IPCA+"
    if ativo.indexador == Indexador.PREFIXADO:
        return "Tesouro Prefixado com Juros Semestrais" if semestrais else "Tesouro Prefixado"
    return None


def atualizar_tesouro(
    sessao: Session,
    hoje: date,
    agora: datetime,
    relatorio: Relatorio,
    baixar: Callable | None = None,
) -> None:
    """Preços dos títulos que o usuário tem (fonte `tesouro`) e a lista dos títulos à venda.
    O arquivo é grande: no máximo uma vez por dia e, sem título na carteira, uma vez por semana."""
    guardados = sessao.scalars(
        select(Ativo).where(Ativo.ativo, Ativo.classe == ClasseAtivo.TESOURO)
    ).all()
    procurados: dict[tuple[str, date], Ativo] = {}
    for ativo in guardados:
        tipo = tipo_do_titulo(ativo)
        if tipo and ativo.vencimento:
            procurados[(tipo, ativo.vencimento)] = ativo
    ultima = preferencias.obter(sessao, TESOURO_BUSCADO)
    if ultima:
        idade = (hoje - date.fromisoformat(ultima)).days
        if idade < 1 or (not procurados and idade < 7):
            return

    desde: dict[int, date] = {}
    existentes: dict[int, set[date]] = {}
    for ativo in procurados.values():
        existentes[ativo.id] = set(
            sessao.scalars(select(Cotacao.data).where(Cotacao.ativo_id == ativo.id))
        )
        primeira = sessao.scalar(
            select(func.min(Operacao.data)).where(Operacao.ativo_id == ativo.id)
        )
        desde[ativo.id] = (primeira or hoje) - timedelta(days=7)

    sessao.commit()  # nada de transação aberta durante o download
    baixar = baixar or tesouro.baixar
    recentes: dict[tuple[str, date], object] = {}
    encontrados: set[tuple[str, date]] = set()
    novos = []
    try:
        for preco in baixar():
            chave = (preco.tipo, preco.vencimento)
            if chave not in recentes or preco.data > recentes[chave].data:
                recentes[chave] = preco
            ativo = procurados.get(chave)
            if ativo is None:
                continue
            encontrados.add(chave)
            if preco.data >= desde[ativo.id] and preco.data not in existentes[ativo.id]:
                existentes[ativo.id].add(preco.data)
                novos.append(
                    Cotacao(ativo_id=ativo.id, data=preco.data, fechamento=preco.pu_venda,
                            fonte="tesouro", obtido_em=agora)
                )  # fmt: skip
    except ERROS_DE_FONTE as erro:
        relatorio.erros["Tesouro Direto"] = str(erro) or type(erro).__name__
        return
    if not recentes:
        return
    sessao.add_all(novos)
    relatorio.cotacoes += len(novos)
    # Só o que ainda é vendido: títulos vencidos saem da lista.
    data_mais_nova = max(p.data for p in recentes.values())
    sessao.execute(delete(TituloTesouro))
    sessao.add_all(
        TituloTesouro(tipo=p.tipo, vencimento=p.vencimento, data_base=p.data, pu_venda=p.pu_venda,
                      taxa_compra=p.taxa_compra, taxa_venda=p.taxa_venda, pu_compra=p.pu_compra)
        for p in recentes.values()
        if p.data == data_mais_nova
    )  # fmt: skip
    preferencias.definir(sessao, TESOURO_BUSCADO, hoje.isoformat())
    for chave, ativo in procurados.items():
        if chave in encontrados:
            relatorio.atualizados.append(ativo.ticker)
            relatorio.fontes[ativo.ticker] = "tesouro"
        else:
            relatorio.erros[ativo.ticker] = (
                f"título não encontrado no Tesouro: {chave[0]} {chave[1]:%d/%m/%Y}"
            )
    sessao.commit()


def titulos_tesouro(sessao: Session) -> list[TituloTesouro]:
    return list(
        sessao.scalars(select(TituloTesouro).order_by(TituloTesouro.tipo, TituloTesouro.vencimento))
    )


def atualizar_ibovespa(sessao: Session, fontes, hoje: date, relatorio: Relatorio) -> int:
    """Ibovespa (E6-05), da fonte que tiver o índice, desde o último dia guardado."""
    fonte = next((f for f in fontes if hasattr(f, "ibovespa")), None)
    if fonte is None:
        return 0
    ultima = sessao.scalar(
        select(func.max(IndiceMacro.data)).where(IndiceMacro.serie == "ibovespa")
    )
    primeira = sessao.scalar(select(func.min(Operacao.data)))
    inicio = (
        ultima + timedelta(days=1) if ultima else min(primeira or hoje, hoje - timedelta(days=400))
    )
    if inicio > hoje:
        return 0
    try:
        pontos = fonte.ibovespa(inicio, hoje)
    except ERROS_DE_FONTE as erro:
        relatorio.erros["Ibovespa"] = str(erro)
        return 0
    guardadas = set(sessao.scalars(select(IndiceMacro.data).where(IndiceMacro.serie == "ibovespa")))
    novos = 0
    for dia, valor in pontos:
        if dia not in guardadas:
            sessao.add(IndiceMacro(serie="ibovespa", data=dia, valor=valor, fonte=fonte.nome))
            novos += 1
    sessao.flush()
    return novos


def atualizar(
    sessao: Session, fontes: Sequence[FonteCotacoes], hoje: date,
    buscar_indice: Callable | None = None, baixar_tesouro: Callable | None = None,
) -> Relatorio:  # fmt: skip
    agora = agora_utc()
    relatorio = Relatorio(em=agora)
    for ativo in ativos_para_atualizar(sessao):
        atualizar_ativo(sessao, ativo, fontes, hoje, agora, relatorio)
    relatorio.indices = atualizar_indices(sessao, hoje, buscar_indice, relatorio)
    relatorio.indices += atualizar_ibovespa(sessao, fontes, hoje, relatorio)
    atualizar_tesouro(sessao, hoje, agora, relatorio, baixar_tesouro)
    preferencias.definir(sessao, STATUS, relatorio.para_json())
    log.info(
        "Mercado atualizado: %s ok, %s com erro", len(relatorio.atualizados), len(relatorio.erros)
    )
    return relatorio


# ───────── Status (E5-07, E5-09) ─────────


@dataclass(frozen=True)
class Status:
    em: datetime | None  # UTC
    fontes: dict[str, str]
    erros: dict[str, str]
    automatico: bool

    @property
    def nunca(self) -> bool:
        return self.em is None


def status(sessao: Session) -> Status:
    bruto = preferencias.obter(sessao, STATUS)
    dados = json.loads(bruto) if bruto else {}
    return Status(
        datetime.fromisoformat(dados["em"]) if dados.get("em") else None,
        dados.get("fontes", {}),
        dados.get("erros", {}),
        preferencias.obter(sessao, AUTOMATICO) != "nao",
    )


def precisa_atualizar(sessao: Session, hoje_utc: date) -> bool:
    """Atualização automática 1× ao dia ao abrir (se ligada e houver o que atualizar)."""
    atual = status(sessao)
    if not atual.automatico or not ativos_para_atualizar(sessao):
        return False
    return atual.em is None or atual.em.date() < hoje_utc


def definir_automatico(sessao: Session, ligado: bool) -> None:
    preferencias.definir(sessao, AUTOMATICO, "sim" if ligado else "nao")


# ───────── Mercado / watchlist (E5-10) ─────────


@dataclass
class Acompanhado:
    ativo: Ativo
    ultima: Cotacao | None
    anterior: Cotacao | None
    serie: list[Decimal]  # fechamentos dos últimos 30 dias, para o mini-gráfico
    na_carteira: bool

    @property
    def variacao(self) -> Decimal | None:
        if not self.ultima or not self.anterior or not self.anterior.fechamento:
            return None
        return (self.ultima.fechamento - self.anterior.fechamento) * 100 / self.anterior.fechamento

    def pontos_svg(self, largura: int = 96, altura: int = 28) -> str:
        """Coordenadas do mini-gráfico (polyline), sem eixo: só a forma da variação."""
        if len(self.serie) < 2:
            return ""
        menor, maior = min(self.serie), max(self.serie)
        faixa = (maior - menor) or Decimal(1)
        passo = Decimal(largura) / (len(self.serie) - 1)
        return " ".join(
            f"{float(passo * i):.1f},{float(altura - (v - menor) / faixa * altura):.1f}"
            for i, v in enumerate(self.serie)
        )


def acompanhados(sessao: Session, hoje: date, com_posicao: set[int]) -> list[Acompanhado]:
    itens = []
    for ativo in ativos_para_atualizar(sessao):
        ultimas = list(
            sessao.scalars(
                select(Cotacao)
                .where(Cotacao.ativo_id == ativo.id, Cotacao.data <= hoje)
                .order_by(Cotacao.data.desc())
                .limit(30)
            )
        )
        itens.append(
            Acompanhado(
                ativo,
                ultimas[0] if ultimas else None,
                ultimas[1] if len(ultimas) > 1 else None,
                [c.fechamento for c in reversed(ultimas)],
                ativo.id in com_posicao,
            )
        )
    return sorted(itens, key=lambda item: (not item.na_carteira, item.ativo.ticker))


@dataclass(frozen=True)
class Indices:
    selic_meta: Decimal | None  # % a.a.
    cdi_12m: Decimal | None  # % acumulado
    ipca_12m: Decimal | None


def _acumulado(valores: list[Decimal]) -> Decimal | None:
    if not valores:
        return None
    fator = Decimal(1)
    for valor in valores:
        fator *= 1 + valor / 100
    return (fator - 1) * 100


def indices(sessao: Session, hoje: date) -> Indices:
    def serie(nome: str, desde: date) -> list[Decimal]:
        return list(
            sessao.scalars(
                select(IndiceMacro.valor)
                .where(
                    IndiceMacro.serie == nome, IndiceMacro.data > desde, IndiceMacro.data <= hoje
                )
                .order_by(IndiceMacro.data)
            )
        )

    meta = sessao.scalar(
        select(IndiceMacro.valor)
        .where(IndiceMacro.serie == "selic_meta", IndiceMacro.data <= hoje + timedelta(days=7))
        .order_by(IndiceMacro.data.desc())
        .limit(1)
    )
    ipca = list(
        sessao.scalars(
            select(IndiceMacro.valor)
            .where(IndiceMacro.serie == "ipca")
            .order_by(IndiceMacro.data.desc())
            .limit(12)
        )
    )
    primeiro_cdi = sessao.scalar(
        select(func.min(IndiceMacro.data)).where(IndiceMacro.serie == "cdi")
    )
    cdi_completo = primeiro_cdi is not None and primeiro_cdi <= hoje - timedelta(days=360)
    return Indices(
        meta,
        _acumulado(serie("cdi", hoje - timedelta(days=365))) if cdi_completo else None,
        _acumulado(ipca) if len(ipca) == 12 else None,
    )

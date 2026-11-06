"""Regras pessoais de investimento e alertas "fora da regra" (E6-06, E6-12).

As regras são configuração (um conjunto só), guardadas em `preferencias` como JSON — não
viraram tabela própria. Campo vazio = regra desligada.
"""

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.models import ClasseAtivo, GrupoAlocacao
from app.domain import alocacao, carteira, dividendos, preferencias, proventos
from app.domain.ativos import ler_decimal
from app.domain.dinheiro import formatar_brl, formatar_percentual
from app.domain.formularios import ErroValidacao, Leitor

CHAVE = "regras_investimento"


@dataclass
class Regras:
    limite_ativo_pct: Decimal | None = None  # ações e FIIs, % da carteira total
    limite_etf_pct: Decimal | None = None
    transicao_patrimonio: int | None = None  # centavos: abaixo disso vale o limite de transição
    transicao_limite_pct: Decimal | None = None
    transicao_max_por_setor: int | None = None  # nº de ações do mesmo setor na transição
    limite_setor_pct: Decimal | None = None  # ações por setor
    limite_tipo_fii_pct: Decimal | None = None  # tijolo ou papel
    papel_max_pct_fiis: Decimal | None = None  # FII de papel, % da parcela de FIIs
    cripto_teto_pct: Decimal | None = None
    dy_min_acoes: Decimal | None = None
    dy_min_fiis: Decimal | None = None
    setores_excluidos: list[str] = field(default_factory=list)
    reserva_primeiro: bool = True

    @property
    def configuradas(self) -> bool:
        return any(v not in (None, [], True) for v in asdict(self).values())


SUGESTAO_MODERADO = Regras(
    limite_ativo_pct=Decimal(5),
    limite_etf_pct=Decimal(8),
    transicao_patrimonio=2_000_000,
    transicao_limite_pct=Decimal(10),
    transicao_max_por_setor=2,
    limite_setor_pct=Decimal(8),
    limite_tipo_fii_pct=Decimal(12),
    papel_max_pct_fiis=Decimal(50),
    cripto_teto_pct=Decimal(2),
    dy_min_acoes=Decimal(6),
    dy_min_fiis=Decimal(8),
    setores_excluidos=["Varejo", "Proteína animal", "Transporte aéreo"],
    reserva_primeiro=True,
)

_DECIMAIS = [
    "limite_ativo_pct",
    "limite_etf_pct",
    "transicao_limite_pct",
    "limite_setor_pct",
    "limite_tipo_fii_pct",
    "papel_max_pct_fiis",
    "cripto_teto_pct",
    "dy_min_acoes",
    "dy_min_fiis",
]


def obter(sessao: Session) -> Regras:
    bruto = preferencias.obter(sessao, CHAVE)
    if not bruto:
        return Regras()
    dados = json.loads(bruto)
    for nome in _DECIMAIS:
        if dados.get(nome) is not None:
            dados[nome] = Decimal(dados[nome])
    return Regras(**dados)


def gravar(sessao: Session, regras: Regras) -> None:
    dados = {
        nome: (format(valor, "f") if isinstance(valor, Decimal) else valor)
        for nome, valor in asdict(regras).items()
    }
    preferencias.definir(sessao, CHAVE, json.dumps(dados, ensure_ascii=False))


def ler_formulario(dados: Mapping[str, str]) -> Regras:
    leitor = Leitor(dados)
    valores = {}
    for nome in _DECIMAIS:
        valor = ler_decimal(leitor, nome, obrigatorio=False, positivo=False)
        if valor is not None and not 0 <= valor <= 100:
            leitor.erros[nome] = "Use de 0 a 100."
        valores[nome] = valor
    valores["transicao_patrimonio"] = leitor.centavos("transicao_patrimonio", obrigatorio=False)
    valores["transicao_max_por_setor"] = leitor.inteiro(
        "transicao_max_por_setor", 1, 20, obrigatorio=False
    )
    setores = leitor.texto("setores_excluidos", maximo=500, obrigatorio=False) or ""
    valores["setores_excluidos"] = [s.strip() for s in setores.split(",") if s.strip()]
    valores["reserva_primeiro"] = leitor.marcado("reserva_primeiro")
    if leitor.erros:
        raise ErroValidacao(leitor.erros)
    return Regras(**valores)


# ───────── Alertas ─────────


@dataclass(frozen=True)
class Alerta:
    nivel: str  # atencao, alerta ou critico (com ícone + texto na tela)
    titulo: str
    detalhe: str
    ativo_id: int | None = None


def _pct(valor: Decimal) -> str:
    return formatar_percentual(valor)


def _tipo_fii(setor: str | None) -> str:
    texto = (setor or "").lower()
    return "papel" if any(p in texto for p in ("papel", "recebív", "recebiv", "cri")) else "tijolo"


def alertas(sessao: Session, hoje: date, cart: carteira.Carteira | None = None) -> list[Alerta]:
    cart = cart or carteira.montar(sessao, hoje)
    regras = obter(sessao)
    situacao = alocacao.alocacao(sessao, hoje, cart)
    total = situacao.total
    lista: list[Alerta] = []

    reserva = alocacao.reserva(sessao, hoje, cart)
    if reserva.alvo and not reserva.completa:
        lista.append(
            Alerta(
                "atencao",
                "Reserva de emergência incompleta",
                f"{formatar_brl(reserva.atual)} de {formatar_brl(reserva.alvo)} "
                f"({reserva.meses} meses de despesa)."
                + (
                    " Pela sua regra, os aportes vão para ela primeiro."
                    if regras.reserva_primeiro
                    else ""
                ),
            )
        )

    for linha in situacao.linhas:
        # Cripto com teto configurado: o alerta do teto (abaixo) já cobre.
        teto_cripto = linha.grupo == GrupoAlocacao.CRIPTO and regras.cripto_teto_pct is not None
        if linha.estado in ("acima", "abaixo") and not teto_cripto:
            lista.append(
                Alerta(
                    "alerta" if abs(linha.desvio) > 2 * (linha.tolerancia or 0) else "atencao",
                    f"{linha.grupo.rotulo} {linha.estado} do alvo",
                    f"{_pct(linha.percentual)} da carteira; alvo {_pct(linha.alvo)} "
                    f"± {linha.tolerancia.normalize():f} pp.",
                )
            )

    if total:
        transicao = regras.transicao_patrimonio is not None and total < regras.transicao_patrimonio
        limite_ativo = regras.transicao_limite_pct if transicao else regras.limite_ativo_pct
        por_setor: dict[str, list] = {}
        por_tipo_fii = {"tijolo": 0, "papel": 0}
        excluidos = [s.lower() for s in regras.setores_excluidos]
        for linha in cart.linhas:
            classe = linha.ativo.classe
            peso = Decimal(linha.valor * 100) / total
            limite = (
                regras.limite_etf_pct
                if classe in (ClasseAtivo.ETF, ClasseAtivo.BDR)
                else limite_ativo
            )
            if (
                classe in (ClasseAtivo.ACAO, ClasseAtivo.FII, ClasseAtivo.ETF, ClasseAtivo.BDR)
                and limite
                and peso > limite
            ):
                lista.append(
                    Alerta(
                        "alerta",
                        f"{linha.ativo.ticker} acima do limite por ativo",
                        f"{_pct(peso)} da carteira; limite {_pct(limite)}"
                        + (
                            " (regra de transição)"
                            if transicao and classe not in (ClasseAtivo.ETF, ClasseAtivo.BDR)
                            else ""
                        )
                        + ". Corrija pelos próximos aportes antes de pensar em vender.",
                        linha.ativo.id,
                    )
                )
            setor = (linha.ativo.setor or "").strip()
            if classe == ClasseAtivo.ACAO:
                por_setor.setdefault(setor or "Sem setor", []).append(linha)
            if classe == ClasseAtivo.FII:
                por_tipo_fii[_tipo_fii(setor)] += linha.valor
            if setor and any(e in setor.lower() for e in excluidos):
                lista.append(
                    Alerta(
                        "critico",
                        f"{linha.ativo.ticker} está num setor que você excluiu",
                        f"Setor “{setor}”.",
                        linha.ativo.id,
                    )
                )
            if classe in (ClasseAtivo.ACAO, ClasseAtivo.FII) and not setor:
                lista.append(
                    Alerta(
                        "atencao",
                        f"{linha.ativo.ticker} sem setor",
                        "Cadastre o setor para os limites por setor valerem.",
                        linha.ativo.id,
                    )
                )
        for setor, linhas in por_setor.items():
            if setor == "Sem setor":
                continue
            peso = Decimal(sum(ln.valor for ln in linhas) * 100) / total
            if (
                transicao
                and regras.transicao_max_por_setor
                and len(linhas) > regras.transicao_max_por_setor
            ):
                lista.append(
                    Alerta(
                        "alerta",
                        f"Muitas ações de {setor}",
                        f"{len(linhas)} ações; na transição, no máximo "
                        f"{regras.transicao_max_por_setor}.",
                    )
                )
            elif not transicao and regras.limite_setor_pct and peso > regras.limite_setor_pct:
                lista.append(
                    Alerta(
                        "alerta",
                        f"Setor {setor} acima do limite",
                        f"{_pct(peso)} da carteira; limite {_pct(regras.limite_setor_pct)}.",
                    )
                )
        fiis = sum(por_tipo_fii.values())
        if not transicao and regras.limite_tipo_fii_pct:
            for tipo, valor in por_tipo_fii.items():
                peso = Decimal(valor * 100) / total
                if peso > regras.limite_tipo_fii_pct:
                    lista.append(
                        Alerta(
                            "alerta",
                            f"FIIs de {tipo} acima do limite",
                            f"{_pct(peso)} da carteira; limite {_pct(regras.limite_tipo_fii_pct)}.",
                        )
                    )
        if fiis and regras.papel_max_pct_fiis:
            peso = Decimal(por_tipo_fii["papel"] * 100) / fiis
            if peso > regras.papel_max_pct_fiis:
                lista.append(
                    Alerta(
                        "atencao",
                        "Muito FII de papel",
                        f"{_pct(peso)} dos FIIs; máximo {_pct(regras.papel_max_pct_fiis)}.",
                    )
                )
        cripto = situacao.linha(GrupoAlocacao.CRIPTO)
        if regras.cripto_teto_pct is not None and cripto.percentual > regras.cripto_teto_pct:
            lista.append(
                Alerta(
                    "alerta",
                    "Cripto acima do teto",
                    f"{_pct(cripto.percentual)} da carteira; teto {_pct(regras.cripto_teto_pct)}.",
                )
            )

    lista.extend(alertas_dividendos(sessao, hoje, cart, regras))
    ordem = {"critico": 0, "alerta": 1, "atencao": 2}
    return sorted(lista, key=lambda a: ordem[a.nivel])


def alertas_dividendos(
    sessao: Session, hoje: date, cart: carteira.Carteira, regras: Regras
) -> list[Alerta]:
    """E6-12: corte/suspensão, yield abaixo do critério e concentração dos proventos."""
    lista = []
    abertas = [ln for ln in cart.linhas if ln.ativo.classe in (ClasseAtivo.ACAO, ClasseAtivo.FII)]
    indicadores = dividendos.indicadores(sessao, [ln.ativo for ln in abertas], hoje)
    por_unidade = proventos.proventos_por_unidade_12m(sessao, hoje)
    for linha in abertas:
        ind = indicadores[linha.ativo.id]
        ticker = linha.ativo.ticker
        if ind.por_unidade_12m_anterior and not ind.por_unidade_12m:
            lista.append(
                Alerta(
                    "critico",
                    f"{ticker} suspendeu os proventos",
                    "Nada pago nos últimos 12 meses; pagou nos 12 anteriores.",
                    linha.ativo.id,
                )
            )
        elif ind.variacao_12m is not None and ind.variacao_12m <= -25:
            lista.append(
                Alerta(
                    "alerta",
                    f"{ticker} cortou os proventos",
                    f"{_pct(ind.variacao_12m)} por unidade em 12 meses × os 12 anteriores.",
                    linha.ativo.id,
                )
            )
        mensal_parado = (
            linha.ativo.classe == ClasseAtivo.FII
            and ind.pagamentos_12m >= 6
            and ind.ultimo_pagamento
            and ind.ultimo_pagamento < hoje - timedelta(days=50)
        )
        if mensal_parado:
            lista.append(
                Alerta(
                    "alerta",
                    f"{ticker} sem rendimento recente",
                    f"Último com data em {ind.ultimo_pagamento:%d/%m/%Y}; costuma pagar todo mês.",
                    linha.ativo.id,
                )
            )
        minimo = (
            regras.dy_min_fiis if linha.ativo.classe == ClasseAtivo.FII else regras.dy_min_acoes
        )
        dy = proventos.dividend_yield(linha, por_unidade)
        if minimo and dy is not None and dy < minimo:
            lista.append(
                Alerta(
                    "atencao",
                    f"{ticker} com yield abaixo do seu mínimo",
                    f"DY de 12 meses {_pct(dy)}; mínimo {_pct(minimo)}.",
                    linha.ativo.id,
                )
            )

    ranking = proventos.ranking_ativos(sessao, hoje - timedelta(days=365), hoje, limite=50)
    if len(ranking) >= 3 and ranking[0].percentual > 40:
        lista.append(
            Alerta(
                "atencao",
                "Proventos concentrados",
                f"{ranking[0].nome} pagou {_pct(ranking[0].percentual)} dos proventos de 12 meses.",
            )
        )
    return lista

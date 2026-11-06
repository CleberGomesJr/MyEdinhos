"""Alocação-alvo, reserva de emergência e rebalanceamento por aporte (E6-03, E6-04, E6-09).

A carteira "total" da alocação = investimentos + contas marcadas como reserva de emergência
(a reserva de emergência faz parte da renda fixa).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ClasseAtivo, GrupoAlocacao, MetaAlocacao
from app.domain import carteira, contas, preferencias, resumos
from app.domain.ativos import ler_decimal
from app.domain.competencia import Competencia
from app.domain.dinheiro import formatar_brl
from app.domain.formularios import ErroValidacao, Leitor

GRUPO_DA_CLASSE = {
    ClasseAtivo.RENDA_FIXA: GrupoAlocacao.RENDA_FIXA,
    ClasseAtivo.TESOURO: GrupoAlocacao.RENDA_FIXA,
    ClasseAtivo.ACAO: GrupoAlocacao.ACOES,
    ClasseAtivo.FII: GrupoAlocacao.FIIS,
    ClasseAtivo.ETF: GrupoAlocacao.EXTERIOR,
    ClasseAtivo.BDR: GrupoAlocacao.EXTERIOR,
    ClasseAtivo.CRIPTO: GrupoAlocacao.CRIPTO,
    ClasseAtivo.FUNDO: GrupoAlocacao.OUTROS,
    ClasseAtivo.OUTRO: GrupoAlocacao.OUTROS,
}

# Sugestão pronta para um perfil moderado (ponto de partida; o usuário ajusta na tela).
SUGESTAO_MODERADO = {
    GrupoAlocacao.RENDA_FIXA: (45, 5),
    GrupoAlocacao.ACOES: (27, 5),
    GrupoAlocacao.FIIS: (20, 5),
    GrupoAlocacao.EXTERIOR: (8, 3),
    GrupoAlocacao.CRIPTO: (0, 2),
    GrupoAlocacao.OUTROS: (0, 2),
}

RESERVA_MESES = "reserva_meses"


def _pct(parte: int, total: int) -> Decimal:
    return Decimal(parte * 100) / Decimal(total) if total else Decimal(0)


# ───────── Metas (E6-02/E6-03) ─────────


def metas(sessao: Session) -> dict[GrupoAlocacao, MetaAlocacao]:
    return {meta.grupo: meta for meta in sessao.scalars(select(MetaAlocacao))}


def salvar_metas(sessao: Session, dados: Mapping[str, str]) -> None:
    """Campos `alvo-<grupo>` e `tolerancia-<grupo>`; a soma dos alvos tem de dar 100%."""
    leitor = Leitor(dados)
    valores = {}
    for grupo in GrupoAlocacao:
        alvo = ler_decimal(leitor, f"alvo-{grupo.value}", obrigatorio=False, positivo=False)
        tolerancia = ler_decimal(
            leitor, f"tolerancia-{grupo.value}", obrigatorio=False, positivo=False
        )
        if alvo is not None and not 0 <= alvo <= 100:
            leitor.erros[f"alvo-{grupo.value}"] = "Use de 0 a 100."
        if tolerancia is not None and not 0 <= tolerancia <= 50:
            leitor.erros[f"tolerancia-{grupo.value}"] = "Use de 0 a 50."
        valores[grupo] = (alvo or Decimal(0), tolerancia if tolerancia is not None else Decimal(5))
    soma = sum(alvo for alvo, _ in valores.values())
    if not leitor.erros and soma != 100:
        leitor.erros["alvo-renda_fixa"] = (
            f"Os alvos somam {soma.normalize():f}%; precisam somar 100%."
        )
    if leitor.erros:
        raise ErroValidacao(leitor.erros)
    existentes = metas(sessao)
    for grupo, (alvo, tolerancia) in valores.items():
        meta = existentes.get(grupo) or MetaAlocacao(grupo=grupo)
        meta.percentual_alvo, meta.tolerancia_pp = alvo, tolerancia
        sessao.add(meta)
    sessao.flush()


# ───────── Reserva de emergência (E6-09) ─────────


@dataclass(frozen=True)
class Reserva:
    meses: int
    despesa_media: int | None  # saídas médias por mês (12 meses fechados com movimento)
    atual: int

    @property
    def alvo(self) -> int | None:
        return self.despesa_media * self.meses if self.despesa_media else None

    @property
    def falta(self) -> int:
        return max((self.alvo or 0) - self.atual, 0)

    @property
    def completa(self) -> bool:
        return self.alvo is not None and self.atual >= self.alvo

    @property
    def percentual(self) -> Decimal:
        return min(_pct(self.atual, self.alvo), Decimal(100)) if self.alvo else Decimal(0)


def despesa_media(sessao: Session, hoje: date) -> int | None:
    """Média das saídas (competência) nos últimos 12 meses fechados que tiveram movimento."""
    fechados = resumos.fluxo(sessao, Competencia.de_data(hoje).anterior(), 12)
    saidas = [resultado.saidas for _, resultado in fechados if resultado.saidas]
    return sum(saidas) // len(saidas) if saidas else None


def reserva(sessao: Session, hoje: date, cart: carteira.Carteira | None = None) -> Reserva:
    cart = cart or carteira.montar(sessao, hoje)
    meses = int(preferencias.obter(sessao, RESERVA_MESES) or 6)
    return Reserva(meses, despesa_media(sessao, hoje), _valor_reserva(sessao, hoje, cart))


def _contas_reserva(sessao: Session, hoje: date) -> int:
    saldos = contas.saldos(sessao, hoje)
    return sum(saldos.get(c.id, 0) for c in contas.listar(sessao) if c.reserva_emergencia)


def _valor_reserva(sessao: Session, hoje: date, cart: carteira.Carteira) -> int:
    ativos = sum(linha.valor for linha in cart.linhas if linha.ativo.reserva_emergencia)
    return _contas_reserva(sessao, hoje) + ativos


def definir_meses_reserva(sessao: Session, meses: int) -> None:
    preferencias.definir(sessao, RESERVA_MESES, str(meses))


# ───────── Alocação atual × alvo (E6-03) ─────────


@dataclass(frozen=True)
class LinhaAlocacao:
    grupo: GrupoAlocacao
    valor: int
    percentual: Decimal
    alvo: Decimal | None
    tolerancia: Decimal | None

    @property
    def desvio(self) -> Decimal | None:
        """Pontos percentuais acima (+) ou abaixo (−) do alvo."""
        return None if self.alvo is None else self.percentual - self.alvo

    @property
    def estado(self) -> str | None:
        """dentro, abaixo ou acima da tolerância."""
        if self.desvio is None:
            return None
        if abs(self.desvio) <= (self.tolerancia or 0):
            return "dentro"
        return "acima" if self.desvio > 0 else "abaixo"


@dataclass(frozen=True)
class Alocacao:
    linhas: list[LinhaAlocacao]
    total: int
    tem_metas: bool

    def linha(self, grupo: GrupoAlocacao) -> LinhaAlocacao:
        return next(ln for ln in self.linhas if ln.grupo == grupo)


def valores_por_grupo(sessao: Session, hoje: date, cart: carteira.Carteira) -> dict:
    valores = {grupo: 0 for grupo in GrupoAlocacao}
    for linha in cart.linhas:
        valores[GRUPO_DA_CLASSE[linha.ativo.classe]] += linha.valor
    valores[GrupoAlocacao.RENDA_FIXA] += _contas_reserva(sessao, hoje)
    return valores


def alocacao(sessao: Session, hoje: date, cart: carteira.Carteira | None = None) -> Alocacao:
    cart = cart or carteira.montar(sessao, hoje)
    valores = valores_por_grupo(sessao, hoje, cart)
    total = sum(valores.values())
    alvos = metas(sessao)
    linhas = [
        LinhaAlocacao(
            grupo, valores[grupo], _pct(valores[grupo], total),
            alvos[grupo].percentual_alvo if grupo in alvos else None,
            alvos[grupo].tolerancia_pp if grupo in alvos else None,
        )
        for grupo in GrupoAlocacao
    ]  # fmt: skip
    return Alocacao(linhas, total, bool(alvos))


# ───────── Rebalanceamento por aporte (E6-04) ─────────


@dataclass(frozen=True)
class Sugestao:
    destino: str  # rótulo do grupo ou "Reserva de emergência"
    valor: int
    slot: int
    motivo: str


def _dividir(total: int, pesos: dict) -> dict:
    """Divide centavos proporcionalmente aos pesos, sem perder nenhum."""
    soma = sum(pesos.values())
    if not soma or total <= 0:
        return {chave: 0 for chave in pesos}
    partes = {chave: total * peso // soma for chave, peso in pesos.items()}
    sobra = total - sum(partes.values())
    for chave in sorted(pesos, key=lambda c: -pesos[c])[:sobra]:
        partes[chave] += 1
    return partes


def sugerir_aporte(
    sessao: Session, aporte: int, hoje: date, reserva_primeiro: bool = True
) -> list[Sugestao]:
    """Onde colocar o aporte: primeiro a reserva (se incompleta e a regra estiver ligada);
    o resto vai para os grupos abaixo do alvo, na proporção do que falta para cada um."""
    cart = carteira.montar(sessao, hoje)
    sugestoes: list[Sugestao] = []
    restante = aporte
    situacao = reserva(sessao, hoje, cart)
    valores = valores_por_grupo(sessao, hoje, cart)
    if reserva_primeiro and situacao.alvo and not situacao.completa:
        para_reserva = min(restante, situacao.falta)
        sugestoes.append(
            Sugestao(
                "Reserva de emergência",
                para_reserva,
                GrupoAlocacao.RENDA_FIXA.cor_slot,
                f"faltam {formatar_brl(situacao.falta)} para {situacao.meses} meses de despesa",
            )
        )
        valores[GrupoAlocacao.RENDA_FIXA] += para_reserva
        restante -= para_reserva
    alvos = metas(sessao)
    if restante <= 0 or not alvos:
        return sugestoes
    total_final = sum(valores.values()) + restante
    falta = {
        grupo: max(int(meta.percentual_alvo * total_final / 100) - valores[grupo], 0)
        for grupo, meta in alvos.items()
    }
    if sum(falta.values()) >= restante:
        partes = _dividir(restante, falta)
        motivo = "abaixo do alvo"
    else:
        extra = _dividir(
            restante - sum(falta.values()),
            {g: int(m.percentual_alvo * 100) for g, m in alvos.items()},
        )
        partes = {grupo: falta[grupo] + extra[grupo] for grupo in alvos}
        motivo = "completa o alvo e segue a proporção"
    for grupo in GrupoAlocacao:
        if partes.get(grupo):
            sugestoes.append(Sugestao(grupo.rotulo, partes[grupo], grupo.cor_slot, motivo))
    return sugestoes

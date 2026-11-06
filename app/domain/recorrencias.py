"""Recorrências: regras que geram lançamentos pendentes até uma data limite.

`gerado_ate` guarda até onde a regra já gerou; por isso uma ocorrência excluída não volta.
"""

from collections.abc import Mapping
from datetime import date, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, joinedload

from app.db.models import Categoria, Frequencia, Lancamento, Recorrencia
from app.domain.cartoes import fatura_da_compra
from app.domain.competencia import Competencia
from app.domain.datas import somar_meses
from app.domain.formularios import Leitor

HORIZONTE_MESES = 12  # gera no máximo um ano à frente


def ocorrencias(frequencia: Frequencia, inicio: date, depois_de: date, ate: date) -> list[date]:
    """Datas da regra em (`depois_de`, `ate`], contadas sempre a partir de `inicio`."""
    datas = []
    passo = 0
    while True:
        if frequencia == Frequencia.SEMANAL:
            dia = inicio + timedelta(weeks=passo)
        else:
            dia = somar_meses(inicio, passo * (12 if frequencia == Frequencia.ANUAL else 1))
        if dia > ate:
            return datas
        if dia > depois_de:
            datas.append(dia)
        passo += 1


def limite_geracao(mes: Competencia, hoje: date) -> date:
    """Gera até o fim do mês exibido (no mínimo o mês atual), sem passar de um ano à frente."""
    alvo = max(mes.ultimo_dia, Competencia.de_data(hoje).ultimo_dia)
    return min(alvo, Competencia.de_data(somar_meses(hoje, HORIZONTE_MESES)).ultimo_dia)


def gerar_pendentes(sessao: Session, ate: date) -> int:
    criados = 0
    regras = sessao.scalars(
        select(Recorrencia).where(Recorrencia.ativa, Recorrencia.gerado_ate < ate)
    )
    for regra in regras:
        limite = min(ate, regra.fim) if regra.fim else ate
        no_cartao = regra.cartao_id is not None
        for dia in ocorrencias(regra.frequencia, regra.inicio, regra.gerado_ate, limite):
            sessao.add(
                Lancamento(
                    data=dia,
                    competencia=str(Competencia.de_data(dia)),
                    tipo=regra.tipo,
                    valor_centavos=regra.valor_centavos,
                    conta_id=regra.conta_id,
                    cartao_id=regra.cartao_id,
                    fatura=fatura_da_compra(sessao, regra.cartao, dia) if no_cartao else None,
                    categoria_id=regra.categoria_id,
                    descricao=regra.descricao,
                    # No cartão não há "a pagar": a cobrança entra na fatura do mês.
                    pago=no_cartao,
                    vencimento=None if no_cartao else dia,
                    recorrencia_id=regra.id,
                )
            )
            criados += 1
        regra.gerado_ate = max(regra.gerado_ate, limite)
    sessao.flush()
    return criados


def proxima(regra: Recorrencia, hoje: date) -> date | None:
    fim = regra.fim or somar_meses(hoje, HORIZONTE_MESES)
    datas = ocorrencias(regra.frequencia, regra.inicio, hoje - timedelta(days=1), fim)
    return datas[0] if datas else None


def listar(sessao: Session) -> list[Recorrencia]:
    return list(
        sessao.scalars(
            select(Recorrencia)
            .options(
                joinedload(Recorrencia.conta),
                joinedload(Recorrencia.cartao),
                joinedload(Recorrencia.categoria),
            )
            .order_by(Recorrencia.ativa.desc(), Recorrencia.descricao)
        )
    )


def _pendentes_futuros(regra: Recorrencia, hoje: date):
    """Ocorrências que ainda não aconteceram. No cartão não há "pendente": vale a data."""
    condicoes = [Lancamento.recorrencia_id == regra.id, Lancamento.data >= hoje]
    if regra.cartao_id is None:
        condicoes.append(~Lancamento.pago)
    return tuple(condicoes)


def atualizar(sessao: Session, regra: Recorrencia, dados: Mapping[str, str], hoje: date) -> int:
    """Muda a regra e os lançamentos pendentes de hoje em diante. Devolve quantos mudaram."""
    leitor = Leitor(dados)
    descricao = leitor.texto("descricao", maximo=120)
    valor = leitor.centavos("valor")
    categoria_id = leitor.id("categoria_id")
    categoria = sessao.get(Categoria, categoria_id) if categoria_id else None
    if categoria_id and (
        categoria is None
        or categoria.tipo.value != regra.tipo.value
        or not (categoria.ativa or categoria.id == regra.categoria_id)
    ):
        leitor.erros["categoria_id"] = "Categoria inválida."
    leitor.verificar()

    regra.descricao = descricao
    regra.valor_centavos = valor
    regra.categoria_id = categoria.id
    resultado = sessao.execute(
        update(Lancamento)
        .where(*_pendentes_futuros(regra, hoje))
        .values(descricao=descricao, valor_centavos=valor, categoria_id=categoria.id)
    )
    sessao.flush()
    return resultado.rowcount


def encerrar(sessao: Session, regra: Recorrencia, hoje: date) -> int:
    """Para a regra hoje e remove os lançamentos pendentes futuros. Devolve quantos removeu."""
    regra.ativa = False
    regra.fim = hoje
    resultado = sessao.execute(
        delete(Lancamento).where(*_pendentes_futuros(regra, hoje), Lancamento.data > hoje)
    )
    sessao.flush()
    return resultado.rowcount

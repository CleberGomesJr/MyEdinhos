"""Lançamentos: entrada, saída e transferência; parcelas e recorrência na criação.

Resumos (entradas/saídas) ignoram transferências: elas só movem dinheiro entre contas.
"""

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.db.models import (
    Cartao,
    Categoria,
    Conta,
    Desfazer,
    Frequencia,
    Lancamento,
    Recorrencia,
    TipoLancamento,
)
from app.domain.cartoes import fatura_da_compra
from app.domain.competencia import Competencia
from app.domain.datas import hoje as data_de_hoje
from app.domain.datas import somar_meses
from app.domain.desfazer import excluir_com_desfazer
from app.domain.formularios import Leitor

ENTRADA, SAIDA, TRANSFERENCIA = TipoLancamento


class Situacao(StrEnum):
    TODOS = "todos"
    A_PAGAR = "a_pagar"  # pendentes do mês
    ATRASADOS = "atrasados"  # pendentes vencidos de qualquer mês

    @property
    def rotulo(self) -> str:
        return {"todos": "Todos", "a_pagar": "A pagar", "atrasados": "Atrasados"}[self.value]


REPETICOES = {
    "nao": "Não repete",
    "parcelado": "Parcelado",
    **{frequencia.value: frequencia.rotulo for frequencia in Frequencia},
}


@dataclass(frozen=True)
class Filtros:
    conta_id: int | None = None
    cartao_id: int | None = None
    categoria_id: int | None = None
    tipo: TipoLancamento | None = None
    busca: str = ""
    situacao: Situacao = Situacao.TODOS

    @classmethod
    def de_parametros(cls, parametros: Mapping[str, str]) -> "Filtros":
        """Parâmetros da URL; valores inválidos são ignorados."""

        def inteiro(nome: str) -> int | None:
            bruto = (parametros.get(nome) or "").strip()
            return int(bruto) if bruto.isdigit() else None

        def opcao(enum, nome: str, padrao):
            try:
                return enum(parametros.get(nome))
            except ValueError:
                return padrao

        # "origem" vem do select da tela: "conta-3" ou "cartao-2".
        origem, _, ident = (parametros.get("origem") or "").partition("-")
        origem_id = int(ident) if ident.isdigit() else None
        return cls(
            conta_id=origem_id if origem == "conta" else inteiro("conta_id"),
            cartao_id=origem_id if origem == "cartao" else inteiro("cartao_id"),
            categoria_id=inteiro("categoria_id"),
            tipo=opcao(TipoLancamento, "tipo", None),
            busca=(parametros.get("busca") or "").strip()[:60],
            situacao=opcao(Situacao, "situacao", Situacao.TODOS),
        )

    @property
    def ativos(self) -> bool:
        return self != Filtros()

    @property
    def origem(self) -> str:
        if self.conta_id:
            return f"conta-{self.conta_id}"
        return f"cartao-{self.cartao_id}" if self.cartao_id else ""


@dataclass
class Totais:
    entradas: int = 0
    saidas: int = 0
    pendentes: int = 0

    @property
    def resultado(self) -> int:
        return self.entradas - self.saidas


def _sem_acento(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn"
    )


def atrasado(lancamento: Lancamento, hoje: date) -> bool:
    return not lancamento.pago and (lancamento.vencimento or lancamento.data) < hoje


def listar(
    sessao: Session, competencia: Competencia, filtros: Filtros, hoje: date
) -> list[Lancamento]:
    consulta = (
        select(Lancamento)
        .options(
            joinedload(Lancamento.conta),
            joinedload(Lancamento.conta_destino),
            joinedload(Lancamento.cartao),
            joinedload(Lancamento.categoria).joinedload(Categoria.pai),
        )
        .order_by(Lancamento.data.desc(), Lancamento.id.desc())
    )
    if filtros.situacao == Situacao.ATRASADOS:
        consulta = consulta.where(
            ~Lancamento.pago, func.coalesce(Lancamento.vencimento, Lancamento.data) < hoje
        )
    else:
        consulta = consulta.where(Lancamento.competencia == str(competencia))
        if filtros.situacao == Situacao.A_PAGAR:
            consulta = consulta.where(~Lancamento.pago)
    if filtros.conta_id:
        consulta = consulta.where(
            or_(
                Lancamento.conta_id == filtros.conta_id,
                Lancamento.conta_destino_id == filtros.conta_id,
            )
        )
    if filtros.cartao_id:
        consulta = consulta.where(Lancamento.cartao_id == filtros.cartao_id)
    if filtros.categoria_id:
        filhas = select(Categoria.id).where(Categoria.pai_id == filtros.categoria_id)
        consulta = consulta.where(
            or_(
                Lancamento.categoria_id == filtros.categoria_id, Lancamento.categoria_id.in_(filhas)
            )
        )
    if filtros.tipo:
        consulta = consulta.where(Lancamento.tipo == filtros.tipo)

    lancamentos = list(sessao.scalars(consulta).unique())
    if filtros.busca:  # em Python: o lower() do SQLite não entende acentos
        termo = _sem_acento(filtros.busca)
        lancamentos = [item for item in lancamentos if termo in _sem_acento(item.descricao)]
    return lancamentos


def contar_atrasados(sessao: Session, hoje: date) -> int:
    return sessao.scalar(
        select(func.count()).where(
            ~Lancamento.pago, func.coalesce(Lancamento.vencimento, Lancamento.data) < hoje
        )
    )


def totais(lancamentos: list[Lancamento]) -> Totais:
    resultado = Totais()
    for item in lancamentos:
        if item.tipo == ENTRADA:
            resultado.entradas += item.valor_centavos
        elif item.tipo == SAIDA:
            resultado.saidas += item.valor_centavos
        if not item.pago:
            resultado.pendentes += 1
    return resultado


def dividir_valor(total: int, partes: int) -> list[int]:
    """Divide centavos sem perder nenhum: os primeiros recebem o centavo que sobra."""
    base, resto = divmod(total, partes)
    return [base + (1 if indice < resto else 0) for indice in range(partes)]


def _conta(sessao: Session, leitor: Leitor, campo: str, atual: int | None = None) -> Conta | None:
    conta_id = leitor.id(campo)
    if conta_id is None:
        return None
    conta = sessao.get(Conta, conta_id)
    if conta is None or (not conta.ativa and conta_id != atual):
        leitor.erros[campo] = "Conta inválida ou arquivada."
        return None
    return conta


def _cartao(sessao: Session, leitor: Leitor, atual: int | None = None) -> Cartao | None:
    cartao_id = leitor.id("cartao_id")
    if cartao_id is None:
        return None
    cartao = sessao.get(Cartao, cartao_id)
    if cartao is None or (not cartao.ativo and cartao_id != atual):
        leitor.erros["cartao_id"] = "Cartão inválido ou arquivado."
        return None
    return cartao


def salvar(
    sessao: Session,
    dados: Mapping[str, str],
    lancamento: Lancamento | None = None,
    hoje: date | None = None,
) -> list[Lancamento]:
    """Cria (podendo gerar parcelas ou uma recorrência) ou edita um lançamento.

    `hoje` só serve para validar a compra retroativa (padrão: o dia de hoje).
    """
    leitor = Leitor(dados)
    tipo = leitor.escolha("tipo", TipoLancamento)
    valor = leitor.centavos("valor")
    descricao = leitor.texto("descricao", maximo=120)
    data = leitor.data("data")
    pago = leitor.marcado("pago")
    vencimento = None if pago else leitor.data("vencimento", obrigatorio=False)
    observacao = leitor.texto("observacao", maximo=500, obrigatorio=False)
    editando = lancamento is not None

    # "Pagar com": conta ou cartão (só entradas e saídas; transferência é entre contas).
    no_cartao = tipo in (ENTRADA, SAIDA) and leitor.bruto("meio") == "cartao"
    conta = cartao = None
    if no_cartao:
        cartao = _cartao(sessao, leitor, lancamento.cartao_id if editando else None)
        pago, vencimento = True, None  # no cartão, o dinheiro sai quando a fatura é paga
    else:
        conta = _conta(sessao, leitor, "conta_id", lancamento.conta_id if editando else None)
    destino = categoria = None
    if tipo == TRANSFERENCIA:
        atual = lancamento.conta_destino_id if editando else None
        destino = _conta(sessao, leitor, "conta_destino_id", atual)
        if conta and destino and conta.id == destino.id:
            leitor.erros["conta_destino_id"] = "Escolha uma conta diferente da de origem."
    elif tipo:
        categoria_id = leitor.id("categoria_id")
        categoria = sessao.get(Categoria, categoria_id) if categoria_id else None
        mantida = editando and lancamento.categoria_id == categoria_id
        if categoria_id and (categoria is None or not (categoria.ativa or mantida)):
            leitor.erros["categoria_id"] = "Categoria inválida ou arquivada."
        elif categoria and categoria.tipo.value != tipo.value:
            leitor.erros["categoria_id"] = f"Escolha uma categoria de {tipo.rotulo.lower()}."

    repeticao, parcelas, pagas = "nao", None, 0
    if not editando and tipo in (ENTRADA, SAIDA):
        repeticao = leitor.bruto("repeticao") or "nao"
        if repeticao not in REPETICOES:
            leitor.erros["repeticao"] = "Opção inválida."
        elif repeticao == "parcelado":
            parcelas = leitor.inteiro("parcelas", 2, 120)
            if parcelas and valor and valor < parcelas:
                leitor.erros["valor"] = f"Valor pequeno demais para {parcelas} parcelas."
            if leitor.marcado("retroativo"):
                # Compra retroativa: `data` é a da parcela atual (a primeira ainda não paga).
                pagas = leitor.inteiro("parcelas_pagas", 1, 119) or 0
                if parcelas and pagas >= parcelas:
                    leitor.erros["parcelas_pagas"] = "Precisa ser menor que o número de parcelas."
                if data and data > (hoje or data_de_hoje()):
                    leitor.erros.setdefault(
                        "data", "Informe a data da parcela atual (hoje ou antes), não uma futura."
                    )
    leitor.verificar()

    campos = {
        "tipo": tipo,
        "descricao": descricao,
        "conta": conta,
        "conta_destino": destino,
        "categoria": categoria,
        "cartao": cartao,
        "observacao": observacao,
    }
    if editando:
        for nome, valor_campo in campos.items():
            setattr(lancamento, nome, valor_campo)
        lancamento.valor_centavos = valor
        lancamento.data = data
        lancamento.competencia = str(Competencia.de_data(data))
        lancamento.pago = pago
        lancamento.vencimento = vencimento
        lancamento.fatura = fatura_da_compra(sessao, cartao, data) if cartao else None
        sessao.flush()
        return [lancamento]

    if parcelas:
        return _criar_parcelas(sessao, campos, valor, data, parcelas, pago, vencimento, pagas)

    recorrencia = None
    if repeticao in {frequencia.value for frequencia in Frequencia}:
        recorrencia = Recorrencia(
            descricao=descricao,
            tipo=tipo,
            valor_centavos=valor,
            conta=conta,
            cartao=cartao,
            categoria=categoria,
            frequencia=Frequencia(repeticao),
            inicio=data,
            gerado_ate=data,
        )
        sessao.add(recorrencia)
    novo = Lancamento(
        **campos,
        valor_centavos=valor,
        data=data,
        competencia=str(Competencia.de_data(data)),
        pago=pago,
        vencimento=vencimento,
        recorrencia=recorrencia,
        fatura=fatura_da_compra(sessao, cartao, data) if cartao else None,
    )
    sessao.add(novo)
    sessao.flush()
    return [novo]


def _criar_parcelas(
    sessao: Session,
    campos: dict,
    total: int,
    data: date,
    quantidade: int,
    pago: bool,
    vencimento: date | None,
    pagas: int = 0,
) -> list[Lancamento]:
    """Uma parcela por mês a partir de `data`; só a primeira pode já estar paga.

    No cartão, cada parcela cai na fatura do seu mês (E2-17) e não fica pendente.

    Compra retroativa (E2-23): as `pagas` primeiras parcelas já foram quitadas antes de serem
    lançadas aqui. `data` passa a ser a da parcela atual (a de número `pagas + 1`); as
    anteriores ficam nos meses anteriores, no mesmo dia, marcadas como retroativas: contam nos
    gastos do mês delas, mas não mexem no saldo de hoje nem deixam fatura em aberto.
    """
    grupo = uuid4().hex
    primeiro_vencimento = vencimento or data  # da primeira parcela ainda não paga
    cartao = campos.get("cartao")
    criadas = []
    for indice, parte in enumerate(dividir_valor(total, quantidade)):
        meses = indice - pagas  # distância da parcela atual (negativo = já passou)
        dia = somar_meses(data, meses)
        retroativa = indice < pagas
        paga = cartao is not None or retroativa or (pago and indice == pagas)
        parcela = Lancamento(
            **campos,
            valor_centavos=parte,
            data=dia,
            competencia=str(Competencia.de_data(dia)),
            pago=paga,
            retroativo=retroativa,
            vencimento=None if paga else somar_meses(primeiro_vencimento, meses),
            parcela_grupo_id=grupo,
            parcela_n=indice + 1,
            parcela_total=quantidade,
            fatura=fatura_da_compra(sessao, cartao, dia) if cartao else None,
        )
        sessao.add(parcela)
        criadas.append(parcela)
    sessao.flush()
    return criadas


def excluir(sessao: Session, lancamento: Lancamento, escopo: str = "este") -> Desfazer:
    """`escopo="proximas"` em uma parcela apaga também as parcelas seguintes."""
    linhas = [lancamento]
    if escopo == "proximas" and lancamento.parcela_grupo_id:
        linhas = list(
            sessao.scalars(
                select(Lancamento).where(
                    Lancamento.parcela_grupo_id == lancamento.parcela_grupo_id,
                    Lancamento.parcela_n >= lancamento.parcela_n,
                )
            )
        )
    descricao = f"“{lancamento.descricao}”"
    if len(linhas) > 1:
        descricao += f" e mais {len(linhas) - 1} parcela(s)"
    return excluir_com_desfazer(sessao, linhas, descricao)


def marcar_pago(sessao: Session, lancamento: Lancamento) -> None:
    lancamento.pago = True
    sessao.flush()

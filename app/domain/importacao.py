"""Importar extrato de conta ou cartão, CSV ou OFX (E2-13).

Nada entra sem revisão: o arquivo vira uma lista em que cada linha já vem marcada (ou não)
para importar, com a categoria sugerida. Reimportar o mesmo arquivo não duplica (`id_externo`);
a mesma data e valor de um lançamento já existente vem desmarcado como possível duplicata.
Pagamento de fatura vem desmarcado: importado como gasto, contaria duas vezes.
"""

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import InvalidOperation

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    Cartao,
    Categoria,
    Conta,
    Desfazer,
    Lancamento,
    TipoCategoria,
    TipoLancamento,
)
from app.domain import lancamentos, preferencias
from app.domain.datas import hoje as data_de_hoje
from app.domain.desfazer import excluir_com_desfazer
from app.domain.formularios import ErroValidacao
from app.domain.planilhas import (
    MAX_LINHAS,
    ErroArquivo,
    Tabela,
    adivinhar_coluna,
    centavos_de,
    decodificar,
    ler_data,
    ler_numero,
    ler_tabela,
    sem_acento,
    texto_de_centavos,
)

LOTE_EXTRATO = "importacao_extrato_lote"
COLUNAS = {
    "data": ("data lanc", "data da compra", "data mov", "data", "date", "dt"),
    "descricao": (
        "descricao",
        "historico",
        "lancamento",
        "estabelecimento",
        "title",
        "titulo",
        "memo",
        "detalhe",
    ),
    "valor": ("valor", "amount", "value", "quantia", "montante"),
    "credito": ("credito", "entrada"),
    "debito": ("debito", "saida"),
}
_PAGAMENTO_DE_FATURA = re.compile(
    r"pagamento.*(fatura|cartao)|pgto.*(fatura|cartao)|(fatura|cartao).*pag(to|amento)"
    r"|pagamento recebido"
)
_SALDO = re.compile(r"^(saldo|s\.? ?do)\b")
_BLOCO_OFX = re.compile(r"<STMTTRN>(.*?)(?:</STMTTRN>|(?=<STMTTRN>)|</BANKTRANLIST>)", re.S | re.I)


@dataclass(frozen=True)
class Transacao:
    data: date
    descricao: str
    valor: int  # centavos; positivo = entrada, negativo = saída
    id_externo: str


@dataclass(frozen=True)
class Mapeamento:
    data: str = ""
    descricao: str = ""
    valor: str = ""
    credito: str = ""
    debito: str = ""
    positivo_e_saida: bool = False  # fatura de cartão costuma trazer a compra como positiva


@dataclass(frozen=True)
class Origem:
    conta: Conta | None = None
    cartao: Cartao | None = None

    @property
    def nome(self) -> str:
        return self.conta.nome if self.conta else self.cartao.nome

    @property
    def chave(self) -> str:
        return f"conta-{self.conta.id}" if self.conta else f"cartao-{self.cartao.id}"


def ler_origem(sessao: Session, texto: str) -> Origem | None:
    tipo, _, ident = (texto or "").partition("-")
    if not ident.isdigit():
        return None
    if tipo == "conta" and (conta := sessao.get(Conta, int(ident))) and conta.ativa:
        return Origem(conta=conta)
    if tipo == "cartao" and (cartao := sessao.get(Cartao, int(ident))) and cartao.ativo:
        return Origem(cartao=cartao)
    return None


def eh_ofx(texto: str) -> bool:
    inicio = texto[:2000].upper()
    return "OFXHEADER" in inicio or "<OFX" in inicio


def adivinhar_mapeamento(tabela: Tabela, origem: Origem) -> Mapeamento:
    achar = {nome: adivinhar_coluna(tabela.cabecalho, c) for nome, c in COLUNAS.items()}
    # Com coluna de valor, crédito e débito são outra coisa (ou o mesmo nome repetido).
    separadas = not achar["valor"] and achar["credito"] and achar["debito"]
    return Mapeamento(
        achar["data"], achar["descricao"], achar["valor"],
        achar["credito"] if separadas else "", achar["debito"] if separadas else "",
        positivo_e_saida=origem.cartao is not None,
    )  # fmt: skip


def _identificador(chave: str, ocorrencias: Counter, transacao_base: str) -> str:
    """Estável entre importações. Linhas idênticas no arquivo (duas compras iguais no mesmo
    dia) são distintas pela ordem em que aparecem."""
    ocorrencias[transacao_base] += 1
    bruto = f"{chave}|{transacao_base}|{ocorrencias[transacao_base]}"
    return "csv:" + hashlib.sha256(bruto.encode("utf-8")).hexdigest()[:32]


def ler_csv(tabela: Tabela, mapa: Mapeamento, chave: str) -> tuple[list[Transacao], dict[int, str]]:
    """(transações, erros por número de linha do arquivo)."""
    indices = {coluna: i for i, coluna in enumerate(tabela.cabecalho)}
    transacoes, erros = [], {}
    ocorrencias: Counter = Counter()

    def celula(linha, coluna):
        return linha[indices[coluna]] if coluna in indices else ""

    for deslocamento, linha in enumerate(tabela.linhas):
        numero = tabela.numero_inicial + deslocamento
        descricao = " ".join(celula(linha, mapa.descricao).split())[:120]
        if _SALDO.match(sem_acento(descricao)):
            continue  # linha de saldo do extrato não é lançamento
        try:
            dia = ler_data(celula(linha, mapa.data))
            if mapa.valor:
                numero_valor = ler_numero(celula(linha, mapa.valor))
                valor = centavos_de(numero_valor)
                valor = -valor if mapa.positivo_e_saida else valor
            else:
                credito = celula(linha, mapa.credito)
                debito = celula(linha, mapa.debito)
                valor = centavos_de(ler_numero(credito)) if credito else 0
                valor -= abs(centavos_de(ler_numero(debito))) if debito else 0
        except (ValueError, InvalidOperation):
            erros[numero] = "Data ou valor que não entendi."
            continue
        if not valor:
            continue
        if not descricao:
            erros[numero] = "Sem descrição."
            continue
        base = f"{dia.isoformat()}|{valor}|{sem_acento(descricao)}"
        transacoes.append(
            Transacao(dia, descricao, valor, _identificador(chave, ocorrencias, base))
        )
    return transacoes, erros


def _campo_ofx(bloco: str, nome: str) -> str:
    achado = re.search(rf"<{nome}>([^<\r\n]*)", bloco, re.I)
    return achado[1].strip() if achado else ""


def ler_ofx(texto: str) -> tuple[list[Transacao], dict[int, str]]:
    """OFX 1 (SGML, sem tags de fechamento) ou 2 (XML). No OFX, negativo já é saída."""
    blocos = _BLOCO_OFX.findall(texto)
    if len(blocos) > MAX_LINHAS:
        raise ErroArquivo(
            f"Arquivo com mais de {MAX_LINHAS} lançamentos. Exporte um período menor."
        )
    transacoes, erros = [], {}
    for numero, bloco in enumerate(blocos, start=1):
        descricao = " ".join((_campo_ofx(bloco, "MEMO") or _campo_ofx(bloco, "NAME")).split())[:120]
        try:
            dia = ler_data(_campo_ofx(bloco, "DTPOSTED")[:8])
            valor = centavos_de(ler_numero(_campo_ofx(bloco, "TRNAMT")))
        except (ValueError, InvalidOperation):
            erros[numero] = "Data ou valor que não entendi."
            continue
        if valor:
            fitid = _campo_ofx(bloco, "FITID") or f"{dia}|{valor}|{numero}"
            transacoes.append(
                Transacao(dia, descricao or "Sem descrição", valor, f"ofx:{fitid}"[:80])
            )
    return transacoes, erros


def ler_arquivo(
    conteudo: bytes, origem: Origem, mapa: Mapeamento | None = None
) -> tuple[list[Transacao], dict[int, str]]:
    texto = decodificar(conteudo)
    if eh_ofx(texto):
        return ler_ofx(texto)
    tabela = ler_tabela(texto)
    return ler_csv(tabela, mapa or adivinhar_mapeamento(tabela, origem), origem.chave)


# ───────── Revisão ─────────


@dataclass
class ItemRevisao:
    indice: int
    transacao: Transacao
    situacao: str  # nova, importada, duplicata, fatura
    categoria_id: int | None
    importar: bool

    @property
    def tipo(self) -> str:
        return "entrada" if self.transacao.valor > 0 else "saida"

    @property
    def motivo(self) -> str:
        return {
            "importada": "Já importada antes",
            "duplicata": "Já há lançamento com a mesma data e valor",
            "fatura": "Parece pagamento de fatura: registre em Cartões, para não contar duas vezes",
            "nova": "",
        }[self.situacao]


def _normalizar(descricao: str) -> str:
    return " ".join(re.sub(r"[\d*#/.\-]+", " ", sem_acento(descricao)).split())


def _ja_lancados(sessao: Session, origem: Origem, dias: set[date]) -> list[Lancamento]:
    filtro = (
        or_(Lancamento.conta_id == origem.conta.id, Lancamento.conta_destino_id == origem.conta.id)
        if origem.conta
        else Lancamento.cartao_id == origem.cartao.id
    )
    return list(sessao.scalars(select(Lancamento).where(filtro, Lancamento.data.in_(dias))))


def _efeito(lancamento: Lancamento, origem: Origem) -> int:
    """Valor com sinal, do ponto de vista da origem (mesma convenção da transação)."""
    valor = lancamento.valor_centavos
    if origem.conta and lancamento.conta_destino_id == origem.conta.id:
        return valor
    return valor if lancamento.tipo == TipoLancamento.ENTRADA else -valor


def _categorias_por_descricao(sessao: Session) -> dict[tuple[str, str], int]:
    """A categoria mais recente usada para cada descrição."""
    linhas = sessao.execute(
        select(Lancamento.descricao, Lancamento.tipo, Lancamento.categoria_id)
        .join(Categoria, Categoria.id == Lancamento.categoria_id)
        .where(Categoria.ativa)
        .order_by(Lancamento.data)
    )
    return {(_normalizar(d), t.value): c for d, t, c in linhas}


def _categoria_padrao(sessao: Session, tipo: TipoCategoria, nome: str) -> int | None:
    return sessao.scalar(
        select(Categoria.id)
        .where(Categoria.tipo == tipo, Categoria.ativa)
        .order_by(Categoria.nome != nome, Categoria.nome)
        .limit(1)
    )


def revisar(sessao: Session, transacoes: list[Transacao], origem: Origem) -> list[ItemRevisao]:
    if not transacoes:
        return []
    existentes = _ja_lancados(sessao, origem, {t.data for t in transacoes})
    ids_externos = {
        linha.id_externo for linha in sessao.scalars(
            select(Lancamento).where(
                Lancamento.id_externo.in_([t.id_externo for t in transacoes]),
                (Lancamento.conta_id == origem.conta.id) if origem.conta
                else (Lancamento.cartao_id == origem.cartao.id),
            )
        )
    }  # fmt: skip
    # Só os lançados por outro caminho contam como possível duplicata.
    iguais = Counter(
        (e.data, _efeito(e, origem)) for e in existentes if e.id_externo not in ids_externos
    )
    categorias = _categorias_por_descricao(sessao)
    padrao = {
        "saida": _categoria_padrao(sessao, TipoCategoria.SAIDA, "Outros gastos"),
        "entrada": _categoria_padrao(sessao, TipoCategoria.ENTRADA, "Outras entradas"),
    }
    itens = []
    for indice, transacao in enumerate(transacoes):
        chave = (transacao.data, transacao.valor)
        tipo = "entrada" if transacao.valor > 0 else "saida"
        if transacao.id_externo in ids_externos:
            situacao = "importada"
        elif iguais[chave] > 0:
            iguais[chave] -= 1
            situacao = "duplicata"
        elif _PAGAMENTO_DE_FATURA.search(sem_acento(transacao.descricao)):
            situacao = "fatura"
        else:
            situacao = "nova"
        sugerida = categorias.get((_normalizar(transacao.descricao), tipo), padrao[tipo])
        itens.append(ItemRevisao(indice, transacao, situacao, sugerida, situacao == "nova"))
    return itens


def confirmar(
    sessao: Session,
    transacoes: list[Transacao],
    origem: Origem,
    escolhas: Mapping[str, str],
    hoje: date | None = None,
) -> list[Lancamento]:
    """Grava as linhas marcadas (`importar-<i>`, `categoria-<i>`). O que já foi importado nunca
    entra de novo, mesmo marcado. Tudo ou nada: com qualquer erro, nada é gravado."""
    criados, erros = [], {}
    for item in revisar(sessao, transacoes, origem):
        if not escolhas.get(f"importar-{item.indice}") or item.situacao == "importada":
            continue
        dados = {
            "tipo": item.tipo,
            "valor": texto_de_centavos(abs(item.transacao.valor)),
            "descricao": item.transacao.descricao,
            "data": item.transacao.data.isoformat(),
            "categoria_id": escolhas.get(f"categoria-{item.indice}", ""),
        }
        if origem.conta:
            dados["conta_id"] = str(origem.conta.id)
            if item.transacao.data <= (hoje or data_de_hoje()):
                dados["pago"] = "1"  # data futura no extrato é agendamento: fica pendente
        else:
            dados |= {"meio": "cartao", "cartao_id": str(origem.cartao.id)}
        try:
            (novo,) = lancamentos.salvar(sessao, dados)
        except ErroValidacao as erro:
            erros[f"categoria-{item.indice}"] = next(iter(erro.erros.values()))
            continue
        novo.id_externo = item.transacao.id_externo
        criados.append(novo)
    if erros:
        raise ErroValidacao(erros)
    sessao.flush()
    return criados


# ───────── Última importação: um arquivo errado sai de uma vez ─────────


def guardar_lote(sessao: Session, chave: str, linhas: list) -> None:
    if linhas:
        preferencias.definir(sessao, chave, json.dumps([linha.id for linha in linhas]))


def ler_lote(sessao: Session, chave: str, modelo) -> list:
    """O que ainda existe da última importação (o usuário pode ter apagado uma parte)."""
    ids = json.loads(preferencias.obter(sessao, chave) or "[]")
    return list(sessao.scalars(select(modelo).where(modelo.id.in_(ids)))) if ids else []


def desfazer_lote(sessao: Session, chave: str, modelo, descricao: str) -> Desfazer | None:
    linhas = ler_lote(sessao, chave, modelo)
    if not linhas:
        return None
    registro = excluir_com_desfazer(sessao, linhas, descricao.format(len(linhas)))
    preferencias.definir(sessao, chave, "[]")
    return registro

"""Importar operações de CSV da corretora (E4-11).

Reconhece os relatórios da Área do Investidor da B3 (Negociação e Movimentação), salvos como
CSV, e colunas parecidas de outras corretoras. Antes de gravar, simula tudo numa transação
desfeita depois: a revisão já mostra se uma venda vai faltar posição, por exemplo.
"""

import hashlib
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Ativo, Operacao, TipoOperacao
from app.db.session import garantir_transacao
from app.domain import ativos, operacoes
from app.domain.formularios import ErroValidacao
from app.domain.planilhas import (
    Tabela,
    adivinhar_coluna,
    centavos_de,
    ler_data,
    ler_numero,
    sem_acento,
    texto_de_centavos,
)

LOTE = "importacao_operacoes_lote"
COLUNAS = {
    "data": ("data do negocio", "data da operacao", "data", "date"),
    "ticker": ("codigo de negociacao", "codigo", "ticker", "produto", "ativo", "papel"),
    "tipo": ("tipo de movimentacao", "movimentacao", "tipo", "operacao", "c/v"),
    "quantidade": ("quantidade", "qtd", "quantity"),
    "preco": ("preco unitario", "preco", "price"),
    "valor": ("valor da operacao", "valor total", "valor", "total"),
    "taxas": ("taxas", "custos", "corretagem", "emolumentos"),
}

# Movimentações que não são operação para a carteira: ficam de fora, com o motivo na revisão.
_IGNORADAS = ("transferencia", "liquidacao", "atualizacao", "leilao", "cessao", "direito",
              "emprestimo", "reembolso", "resgate", "vencimento")  # fmt: skip
# Renda fixa precisa de indexador e vencimento, que o arquivo não traz.
_RENDA_FIXA = ("TESOURO", "CDB", "LCI", "LCA", "LC", "CRI", "CRA", "RDB", "LIG", "DEB", "DEBENTURE")
ORDEM_NO_DIA = {
    TipoOperacao.POSICAO_INICIAL: 0, TipoOperacao.DESDOBRAMENTO: 1, TipoOperacao.BONIFICACAO: 2,
    TipoOperacao.COMPRA: 3, TipoOperacao.VENDA: 4,
}  # fmt: skip


def tipo_da_movimentacao(texto: str) -> TipoOperacao | None:
    nome = sem_acento(texto)
    if nome in ("c", "compra", "buy") or nome.startswith("compra"):
        return TipoOperacao.COMPRA
    if nome in ("v", "venda", "sell") or nome.startswith("venda"):
        return TipoOperacao.VENDA
    if "dividendo" in nome:
        return TipoOperacao.DIVIDENDO
    if "juros sobre capital" in nome or nome == "jcp":
        return TipoOperacao.JCP
    if "rendimento" in nome:
        return TipoOperacao.RENDIMENTO
    if "bonificacao" in nome:
        return TipoOperacao.BONIFICACAO
    return None


def ticker_do_produto(texto: str) -> str:
    """'ITSA4 - ITAUSA S/A' → ITSA4; 'ITSA4F' (fracionário) → ITSA4."""
    codigo = re.split(r"[\s\-–]", texto.strip().upper(), maxsplit=1)[0]
    return re.sub(r"^([A-Z]{4}\d{1,2})F$", r"\1", codigo)


@dataclass(frozen=True)
class Mapeamento:
    data: str = ""
    ticker: str = ""
    tipo: str = ""
    quantidade: str = ""
    preco: str = ""
    valor: str = ""
    taxas: str = ""


def adivinhar_mapeamento(tabela: Tabela) -> Mapeamento:
    return Mapeamento(**{n: adivinhar_coluna(tabela.cabecalho, c) for n, c in COLUNAS.items()})


@dataclass
class LinhaOperacao:
    numero: int  # linha no arquivo
    original: str  # texto da movimentação, para mostrar
    ticker: str = ""
    tipo: TipoOperacao | None = None
    data: date | None = None
    quantidade: Decimal | None = None
    preco: Decimal | None = None
    valor: int = 0
    taxas: int = 0
    id_externo: str = ""
    situacao: str = "nova"  # nova, importada, duplicata, ignorada, erro
    motivo: str = ""
    ativo_novo: bool = field(default=False)

    @property
    def importavel(self) -> bool:
        return self.situacao in ("nova", "duplicata")


def ler_linhas(tabela: Tabela, mapa: Mapeamento) -> list[LinhaOperacao]:
    indices = {coluna: i for i, coluna in enumerate(tabela.cabecalho)}
    ocorrencias: Counter = Counter()
    linhas = []

    def celula(linha, coluna):
        return linha[indices[coluna]] if coluna in indices else ""

    def numero_ou_nada(texto):
        limpo = texto.strip()
        return ler_numero(limpo) if limpo and limpo != "-" else None

    for deslocamento, bruta in enumerate(tabela.linhas):
        original = celula(bruta, mapa.tipo)
        item = LinhaOperacao(tabela.numero_inicial + deslocamento, original)
        item.ticker = ticker_do_produto(celula(bruta, mapa.ticker))
        item.tipo = tipo_da_movimentacao(original)
        linhas.append(item)
        if item.tipo is None:
            nome = sem_acento(original)
            item.situacao = "ignorada"
            if "desdobro" in nome or "grupamento" in nome:
                item.motivo = "Lance à mão como desdobramento: o arquivo não traz o fator."
            elif any(palavra in nome for palavra in _IGNORADAS):
                item.motivo = "Não muda a carteira (ou já vem na negociação)."
            else:
                item.motivo = "Tipo de movimentação que não reconheço."
            continue
        if item.ticker in _RENDA_FIXA:
            item.situacao = "ignorada"
            item.motivo = "Renda fixa: cadastre à mão, com indexador e vencimento."
            continue
        try:
            item.data = ler_data(celula(bruta, mapa.data))
            item.quantidade = numero_ou_nada(celula(bruta, mapa.quantidade))
            item.preco = numero_ou_nada(celula(bruta, mapa.preco))
            total = numero_ou_nada(celula(bruta, mapa.valor))
            taxas = numero_ou_nada(celula(bruta, mapa.taxas))
        except (ValueError, InvalidOperation):
            item.situacao, item.motivo = "erro", "Data ou número que não entendi."
            continue
        item.valor = abs(centavos_de(total)) if total is not None else 0
        item.taxas = abs(centavos_de(taxas)) if taxas is not None else 0
        if item.quantidade is not None:
            item.quantidade = abs(item.quantidade)
        if item.tipo.provento:
            if not item.valor and item.quantidade and item.preco:
                item.valor = centavos_de(item.quantidade * item.preco)
        elif item.preco is None and item.valor and item.quantidade:
            # Só veio o total.
            item.preco = (Decimal(item.valor) / 100 / item.quantidade).quantize(Decimal("0.000001"))
        if not item.ticker:
            item.situacao, item.motivo = "erro", "Sem o código do ativo."
            continue
        base = "|".join(
            str(parte)
            for parte in (
                item.ticker, item.data, item.tipo.value, item.quantidade, item.preco, item.valor
            )
        )  # fmt: skip
        ocorrencias[base] += 1
        bruto = f"{base}|{ocorrencias[base]}".encode()
        item.id_externo = "csv:" + hashlib.sha256(bruto).hexdigest()[:32]
    return linhas


def _marcar_duplicatas(sessao: Session, linhas: list[LinhaOperacao]) -> None:
    ids = [ln.id_externo for ln in linhas if ln.id_externo]
    importadas = set(
        sessao.scalars(select(Operacao.id_externo).where(Operacao.id_externo.in_(ids)))
    )
    existentes = sessao.scalars(
        select(Operacao).join(Ativo, Ativo.id == Operacao.ativo_id).where(
            Ativo.ticker.in_({ln.ticker for ln in linhas if ln.ticker}),
            Operacao.data.in_({ln.data for ln in linhas if ln.data}),
            Operacao.id_externo.is_(None),
        )
    )  # fmt: skip
    # Mesmo ativo, data, tipo e quantidade (ou valor, nos proventos) lançados à mão.
    iguais = Counter(
        (op.ativo.ticker, op.data, op.tipo, None if op.tipo.provento else op.quantidade,
         op.valor_total_centavos if op.tipo.provento else None)
        for op in existentes
    )  # fmt: skip
    for linha in linhas:
        if linha.situacao != "nova":
            continue
        if linha.id_externo in importadas:
            linha.situacao, linha.motivo = "importada", "Já importada antes."
            continue
        provento = linha.tipo.provento
        chave = (linha.ticker, linha.data, linha.tipo, None if provento else linha.quantidade,
                 linha.valor if provento else None)  # fmt: skip
        if iguais[chave] > 0:
            iguais[chave] -= 1
            linha.situacao = "duplicata"
            linha.motivo = "Já há operação igual, lançada à mão."


def _dados(linha: LinhaOperacao, ativo: Ativo, conta_id: str) -> dict:
    def numero(valor):
        return format(valor, "f").replace(".", ",") if valor is not None else ""

    dados = {
        "ativo_id": str(ativo.id),
        "tipo": linha.tipo.value,
        "data": linha.data.isoformat(),
        "quantidade": numero(linha.quantidade),
        "preco_unitario": numero(linha.preco),
    }
    if linha.tipo.provento:
        dados["valor"] = texto_de_centavos(linha.valor)
    if linha.taxas:
        dados["taxas"] = texto_de_centavos(linha.taxas)
    if conta_id and linha.tipo in operacoes.MOVEM_CONTA:
        dados["conta_id"] = conta_id
    return dados


def _aplicar(sessao: Session, linhas: list[LinhaOperacao], conta_id: str) -> list[Operacao]:
    """Grava em ordem de data (a venda enxerga a compra do mesmo dia). Cada linha num savepoint:
    a que não passa nas regras vira erro nela, sem atrapalhar as outras."""
    garantir_transacao(sessao)
    criadas = []
    ordenadas = sorted(linhas, key=lambda ln: (ln.data, ORDEM_NO_DIA.get(ln.tipo, 5), ln.numero))
    for linha in ordenadas:
        try:
            with sessao.begin_nested():
                ativo = ativos.por_ticker(sessao, linha.ticker)
                if ativo is None:
                    linha.ativo_novo = True
                    ativo = Ativo(
                        ticker=linha.ticker, nome=linha.ticker,
                        classe=ativos.classe_provavel(linha.ticker),
                    )  # fmt: skip
                    sessao.add(ativo)
                    sessao.flush()
                operacao = operacoes.salvar(sessao, _dados(linha, ativo, conta_id))
                operacao.id_externo = linha.id_externo
                sessao.flush()
            criadas.append(operacao)
        except ErroValidacao as erro:
            linha.situacao = "erro"
            linha.motivo = next(iter(erro.erros.values()))
    return criadas


def revisar(
    sessao: Session, linhas: list[LinhaOperacao], conta_id: str = ""
) -> list[LinhaOperacao]:
    """Marca duplicatas e simula a gravação; o banco fica como estava."""
    _marcar_duplicatas(sessao, linhas)
    _aplicar(sessao, [ln for ln in linhas if ln.situacao == "nova"], conta_id)
    sessao.rollback()
    novos = {ln.ticker for ln in linhas if ln.ativo_novo}
    for linha in linhas:
        linha.ativo_novo = linha.ticker in novos and linha.importavel
    return linhas


def confirmar(
    sessao: Session, linhas: list[LinhaOperacao], escolhas: Mapping[str, str], conta_id: str = ""
) -> tuple[list[Operacao], list[LinhaOperacao]]:
    """(criadas, linhas marcadas que não passaram nas regras). `importar-<número da linha>`."""
    _marcar_duplicatas(sessao, linhas)
    marcadas = [ln for ln in linhas if ln.importavel and escolhas.get(f"importar-{ln.numero}")]
    criadas = _aplicar(sessao, marcadas, conta_id)
    return criadas, [ln for ln in marcadas if ln.situacao == "erro"]

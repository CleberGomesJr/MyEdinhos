"""Exportação de lançamentos em CSV (E3-11), no formato que o Excel em pt-BR abre direto:
separador ";", vírgula decimal e BOM UTF-8."""

import csv
import io

from app.db.models import Lancamento, TipoLancamento

CABECALHO = [
    "Data", "Competência", "Descrição", "Tipo", "Categoria", "Conta ou cartão", "Destino",
    "Valor", "Situação", "Parcela", "Observação",
]  # fmt: skip

_INICIO_DE_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def protegido_para_planilha(valor: str | None) -> str:
    """Texto livre não pode virar fórmula na planilha (injeção de CSV)."""
    valor = valor or ""
    return "'" + valor if valor.startswith(_INICIO_DE_FORMULA) else valor


def _valor(centavos: int) -> str:
    """Sem separador de milhar, para a planilha reconhecer como número: -1234,56."""
    reais, resto = divmod(abs(centavos), 100)
    return f"{'-' if centavos < 0 else ''}{reais},{resto:02d}"


def _sinal(item: Lancamento) -> int:
    if item.tipo == TipoLancamento.SAIDA:
        return -item.valor_centavos
    return item.valor_centavos  # entrada; transferência fica positiva (só move dinheiro)


def _origem(item: Lancamento) -> str:
    if item.conta:
        return item.conta.nome
    return item.cartao.nome if item.cartao else ""


def _destino(item: Lancamento) -> str:
    if item.conta_destino:
        return item.conta_destino.nome
    return item.cartao.nome if item.pagamento_de_fatura else ""


def csv_lancamentos(itens: list[Lancamento]) -> str:
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";", lineterminator="\r\n")
    escritor.writerow(CABECALHO)
    for item in sorted(itens, key=lambda i: (i.data, i.id)):
        escritor.writerow(
            [
                item.data.strftime("%d/%m/%Y"),
                item.competencia,
                protegido_para_planilha(item.descricao),
                "Pagamento de fatura" if item.pagamento_de_fatura else item.tipo.rotulo,
                protegido_para_planilha(item.categoria.nome_completo if item.categoria else ""),
                protegido_para_planilha(_origem(item)),
                protegido_para_planilha(_destino(item)),
                _valor(_sinal(item)),
                "Pago" if item.pago else "Pendente",
                f"{item.parcela_n}/{item.parcela_total}" if item.parcela_total else "",
                protegido_para_planilha(item.observacao),
            ]
        )
    return "﻿" + saida.getvalue()

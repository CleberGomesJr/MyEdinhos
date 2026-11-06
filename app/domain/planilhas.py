"""Leitura de arquivos de bancos e corretoras: texto, tabela CSV, números e datas.

Cada banco exporta de um jeito: separador, codificação, linhas soltas antes do cabeçalho e
"1.234,56" ou "1234.56". Nada aqui passa por float.
"""

import csv
import io
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

SEPARADORES = (";", ",", "\t", "|")
FORMATOS_DATA = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%Y%m%d")
MAX_LINHAS = 2000
ASSINATURA_ZIP = b"PK\x03\x04"  # .xlsx é um zip


class ErroArquivo(Exception):
    """Arquivo recusado; a mensagem é mostrada ao usuário."""


def decodificar(conteudo: bytes) -> str:
    if conteudo.startswith(ASSINATURA_ZIP):
        raise ErroArquivo("Arquivo do Excel (.xlsx): no Excel, use Salvar como → CSV e envie esse.")
    try:
        return conteudo.decode("utf-8-sig")
    except UnicodeDecodeError:
        return conteudo.decode("cp1252", errors="replace")


def sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in decomposto if unicodedata.category(c) != "Mn").strip()


@dataclass(frozen=True)
class Tabela:
    cabecalho: list[str]
    linhas: list[list[str]]  # só as com o mesmo número de colunas do cabeçalho
    numero_inicial: int = 2


def ler_tabela(texto: str) -> Tabela:
    """Escolhe o separador que dá a tabela mais consistente. Linhas soltas antes do cabeçalho
    (nome do banco, período) e depois dos dados (saldo, rodapé) ficam de fora."""
    melhor: tuple[int, int, str] | None = None
    for separador in SEPARADORES:
        linhas = list(csv.reader(io.StringIO(texto), delimiter=separador))
        contagem = Counter(len(linha) for linha in linhas if len(linha) >= 2)
        if contagem:
            colunas, vezes = contagem.most_common(1)[0]
            if melhor is None or vezes > melhor[0] or (vezes == melhor[0] and colunas > melhor[1]):
                melhor = (vezes, colunas, separador)
    if melhor is None:
        raise ErroArquivo("Não encontrei uma tabela no arquivo. Ele é mesmo um CSV?")
    _, colunas, separador = melhor
    todas = list(csv.reader(io.StringIO(texto), delimiter=separador))
    indice = next(i for i, linha in enumerate(todas) if len(linha) == colunas)
    dados = [
        [celula.strip() for celula in linha]
        for linha in todas[indice + 1 :]
        if len(linha) == colunas and any(celula.strip() for celula in linha)
    ]
    if not dados:
        raise ErroArquivo("O arquivo só tem o cabeçalho, sem nenhuma linha de dados.")
    if len(dados) > MAX_LINHAS:
        raise ErroArquivo(f"Arquivo com mais de {MAX_LINHAS} linhas. Exporte um período menor.")
    return Tabela([celula.strip() for celula in todas[indice]], dados, indice + 2)


def adivinhar_coluna(cabecalho: list[str], candidatos: tuple[str, ...]) -> str:
    """Primeira coluna cujo nome contém um candidato, na ordem de preferência dos candidatos."""
    nomes = [(coluna, sem_acento(coluna)) for coluna in cabecalho]
    for candidato in candidatos:
        for coluna, nome in nomes:
            if candidato in nome:
                return coluna
    return ""


def ler_data(texto: str) -> date:
    """Levanta ValueError. Ignora hora no fim ("01/10/2026 12:33", "2026-10-01T12:33:00")."""
    bruto = re.split(r"[\sT]", texto.strip())[0]
    for formato in FORMATOS_DATA:
        try:
            return datetime.strptime(bruto, formato).date()
        except ValueError:
            continue
    raise ValueError(texto)


def ler_numero(texto: str) -> Decimal:
    """'1.234,56', '1234.56', '1,234.56', '-10', '(10,00)', '10,00-', 'R$ 10', '10,00 D'.
    D = débito (negativo), C = crédito. Levanta ValueError."""
    limpo = re.sub(r"\s", "", texto.replace("R$", "").replace("\xa0", " ")).upper()
    negativo = False
    if limpo.endswith("D"):
        negativo, limpo = True, limpo[:-1]
    elif limpo.endswith("C"):
        limpo = limpo[:-1]
    if limpo.startswith("(") and limpo.endswith(")"):
        negativo, limpo = True, limpo[1:-1]
    if limpo.endswith("-"):
        negativo, limpo = True, limpo[:-1]
    if limpo[:1] in ("-", "−"):
        negativo, limpo = not negativo, limpo[1:]
    elif limpo.startswith("+"):
        limpo = limpo[1:]
    if "," in limpo and "." in limpo:
        # O último separador é o decimal.
        milhar = "." if limpo.rfind(",") > limpo.rfind(".") else ","
        limpo = limpo.replace(milhar, "").replace(",", ".")
    elif "," in limpo:
        limpo = (
            limpo.replace(",", "")
            if re.fullmatch(r"\d{1,3}(,\d{3})+", limpo)
            else limpo.replace(",", ".")
        )
    elif re.fullmatch(r"[1-9]\d{0,2}(\.\d{3})+", limpo):
        limpo = limpo.replace(".", "")  # pt-BR: 1.500 = mil e quinhentos
    if not re.fullmatch(r"\d*\.?\d+", limpo):
        raise ValueError(texto)
    try:
        valor = Decimal(limpo)
    except InvalidOperation as erro:
        raise ValueError(texto) from erro
    return -valor if negativo else valor


def texto_de_centavos(centavos: int) -> str:
    """Para os formulários do domínio: 123456 → '1234,56'."""
    return f"{centavos // 100},{centavos % 100:02d}"


def centavos_de(valor: Decimal) -> int:
    return int((valor * 100).quantize(Decimal(1), rounding="ROUND_HALF_EVEN"))

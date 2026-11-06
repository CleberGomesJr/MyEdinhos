"""Cadastro de ativos (E4-02) e posição inicial em lote (E4-15)."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Ativo, ClasseAtivo, Indexador, Operacao, TipoOperacao
from app.domain.formularios import ErroValidacao, Leitor

_TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,19}")


def normalizar_ticker(texto: str) -> str:
    return texto.strip().upper().removesuffix(".SA")


def classe_provavel(ticker: str) -> ClasseAtivo:
    """Palpite pelo código da B3: final 11 = FII (ou ETF/unit), 3–8 = ação, 32–35 = BDR."""
    if re.fullmatch(r"[A-Z]{4}(32|33|34|35)", ticker):
        return ClasseAtivo.BDR
    if re.fullmatch(r"[A-Z]{4}11", ticker):
        return ClasseAtivo.FII
    if re.fullmatch(r"[A-Z]{4}[3-8]", ticker):
        return ClasseAtivo.ACAO
    if ticker in {"BTC", "ETH", "SOL", "USDT", "USDC", "XRP", "ADA", "BNB", "DOGE"}:
        return ClasseAtivo.CRIPTO
    return ClasseAtivo.OUTRO


def decimal_de_texto(texto: str) -> Decimal:
    """'1.234,5678', '310.000' (milhar) ou '9.80' → Decimal. Levanta ValueError."""
    limpo = texto.strip().replace("R$", "").replace(" ", "")
    if "," in limpo or re.fullmatch(r"-?[1-9]\d{0,2}(\.\d{3})+", limpo):
        # pt-BR: vírgula decimal; ponto seguido de grupos de 3 dígitos é milhar (310.000)
        limpo = limpo.replace(".", "").replace(",", ".")
    try:
        valor = Decimal(limpo)
    except InvalidOperation as erro:
        raise ValueError(texto) from erro
    if not valor.is_finite():
        raise ValueError(texto)
    return valor


def ler_decimal(leitor: Leitor, campo: str, obrigatorio: bool = True, positivo: bool = True):
    bruto = leitor.bruto(campo)
    if not bruto:
        if obrigatorio:
            leitor.erros[campo] = "Obrigatório."
        return None
    try:
        valor = decimal_de_texto(bruto)
    except ValueError:
        leitor.erros[campo] = "Número inválido. Use vírgula para decimais: 12,5."
        return None
    if positivo and valor <= 0:
        leitor.erros[campo] = "Informe um número maior que zero."
        return None
    return valor


def listar(sessao: Session, incluir_inativos: bool = False) -> list[Ativo]:
    consulta = select(Ativo).order_by(Ativo.ticker)
    if not incluir_inativos:
        consulta = consulta.where(Ativo.ativo)
    return list(sessao.scalars(consulta))


def por_ticker(sessao: Session, ticker: str) -> Ativo | None:
    return sessao.scalar(select(Ativo).where(func.upper(Ativo.ticker) == ticker.upper()))


def salvar(sessao: Session, dados: Mapping[str, str], ativo: Ativo | None = None) -> Ativo:
    leitor = Leitor(dados)
    ticker = normalizar_ticker(leitor.bruto("ticker"))
    if not ticker:
        leitor.erros["ticker"] = "Obrigatório."
    elif not _TICKER.fullmatch(ticker):
        leitor.erros["ticker"] = "Use letras, números, ponto ou hífen (ex.: ITSA4, CDB-BANCO-X)."
    elif (existente := por_ticker(sessao, ticker)) and existente is not ativo:
        leitor.erros["ticker"] = "Já existe um ativo com esse código."
    nome = leitor.texto("nome", maximo=120, obrigatorio=False) or ticker
    classe = leitor.escolha("classe", ClasseAtivo)
    setor = leitor.texto("setor", maximo=60, obrigatorio=False)
    indexador = taxa = vencimento = None
    if classe and classe.renda_fixa:
        if leitor.bruto("indexador"):
            indexador = leitor.escolha("indexador", Indexador)
            taxa = ler_decimal(
                leitor, "taxa", obrigatorio=indexador != Indexador.SELIC, positivo=False
            )
            if indexador == Indexador.SELIC and taxa is None:
                taxa = Decimal(100)
        vencimento = leitor.data("vencimento", obrigatorio=False)
    leitor.verificar()

    if ativo is None:
        ativo = Ativo()
        sessao.add(ativo)
    ativo.ticker, ativo.nome, ativo.classe, ativo.setor = ticker, nome, classe, setor
    ativo.indexador, ativo.taxa, ativo.vencimento = indexador, taxa, vencimento
    ativo.reserva_emergencia = bool(classe and classe.renda_fixa) and leitor.marcado(
        "reserva_emergencia"
    )
    sessao.flush()
    return ativo


def definir_ativo(sessao: Session, ativo: Ativo, valor: bool) -> None:
    ativo.ativo = valor
    sessao.flush()


# ───────── Posição inicial em lote (E4-15) ─────────


@dataclass(frozen=True)
class LinhaPosicao:
    numero: int
    ticker: str
    quantidade: Decimal
    preco_medio: Decimal


def ler_lote(texto: str) -> tuple[list[LinhaPosicao], dict[int, str]]:
    """Uma linha por ativo: `TICKER; QUANTIDADE; PREÇO MÉDIO` (aceita ; tab ou espaços).
    Devolve as linhas válidas e os erros por número de linha."""
    linhas, erros = [], {}
    vistos: set[str] = set()
    for numero, bruta in enumerate(texto.splitlines(), start=1):
        if not bruta.strip() or bruta.strip().startswith("#"):
            continue
        partes = [p for p in re.split(r"[;\t]|\s{1,}", bruta.strip()) if p]
        if len(partes) != 3:
            erros[numero] = "Use TICKER; QUANTIDADE; PREÇO MÉDIO."
            continue
        ticker = normalizar_ticker(partes[0])
        try:
            quantidade, preco = decimal_de_texto(partes[1]), decimal_de_texto(partes[2])
        except ValueError:
            erros[numero] = "Quantidade ou preço inválido."
            continue
        if not _TICKER.fullmatch(ticker):
            erros[numero] = f"Código inválido: {partes[0]}."
        elif quantidade <= 0 or preco < 0:
            erros[numero] = "Quantidade deve ser maior que zero e preço não pode ser negativo."
        elif ticker in vistos:
            erros[numero] = f"{ticker} aparece mais de uma vez."
        else:
            vistos.add(ticker)
            linhas.append(LinhaPosicao(numero, ticker, quantidade, preco))
    return linhas, erros


def importar_posicao_inicial(sessao: Session, dados: Mapping[str, str]) -> list[Operacao]:
    """Cria os ativos que faltam e uma operação `posicao_inicial` para cada linha.
    Tudo ou nada: com qualquer erro, nada é gravado."""
    leitor = Leitor(dados)
    data: date | None = leitor.data("data")
    linhas, erros_linhas = ler_lote(leitor.bruto("linhas"))
    if not linhas and not erros_linhas:
        leitor.erros["linhas"] = "Cole pelo menos uma linha."
    elif erros_linhas:
        leitor.erros["linhas"] = " ".join(f"Linha {n}: {msg}" for n, msg in erros_linhas.items())
    if data and not leitor.erros:
        for linha in linhas:
            ativo = por_ticker(sessao, linha.ticker)
            if ativo and sessao.scalar(
                select(Operacao.id).where(
                    Operacao.ativo_id == ativo.id, Operacao.tipo == TipoOperacao.POSICAO_INICIAL
                )
            ):
                leitor.erros["linhas"] = (
                    f"{linha.ticker} já tem posição inicial. Edite-a na página do ativo."
                )
                break
    if leitor.erros:
        raise ErroValidacao(leitor.erros)

    criadas = []
    for linha in linhas:
        ativo = por_ticker(sessao, linha.ticker)
        if ativo is None:
            ativo = Ativo(
                ticker=linha.ticker, nome=linha.ticker, classe=classe_provavel(linha.ticker)
            )
            sessao.add(ativo)
        operacao = Operacao(
            ativo=ativo,
            data=data,
            tipo=TipoOperacao.POSICAO_INICIAL,
            quantidade=linha.quantidade,
            preco_unitario=linha.preco_medio,
            taxas_centavos=0,
            valor_total_centavos=centavos(linha.quantidade * linha.preco_medio),
        )
        sessao.add(operacao)
        criadas.append(operacao)
    sessao.flush()
    return criadas


def centavos(reais: Decimal) -> int:
    """Reais (Decimal) → centavos inteiros, arredondando meio para o par."""
    return int((reais * 100).quantize(Decimal(1), rounding="ROUND_HALF_EVEN"))

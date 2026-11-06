"""Guia visual (`/design`): paleta, tipografia e componentes, com dados fictícios."""

from decimal import Decimal

from fastapi import APIRouter, Request, Response

from app.web import eventos_htmx, templates

router = APIRouter()

CORES_DADOS = [
    ("--s1", "Série 1 · patrimônio"),
    ("--s2", "Série 2"),
    ("--s3", "Série 3 · entrada"),
    ("--s4", "Série 4"),
    ("--s5", "Série 5"),
    ("--s6", "Série 6"),
    ("--s7", "Série 7"),
    ("--s8", "Série 8 · saída"),
    ("--s-outros", "Outros"),
]
CORES_ESTADO = [
    ("--ok", "OK"),
    ("--atencao", "Atenção"),
    ("--alerta", "Alerta"),
    ("--critico", "Crítico"),
]
SUPERFICIES = [
    ("--bg", "Fundo"),
    ("--surface-1", "Superfície 1"),
    ("--surface-2", "Superfície 2"),
    ("--surface-3", "Superfície 3"),
    ("--text", "Texto"),
    ("--text-2", "Texto 2"),
    ("--text-3", "Texto 3"),
]
TAMANHOS = [36, 28, 20, 16, 14, 13, 12]

MESES = ["mai/26", "jun/26", "jul/26", "ago/26", "set/26", "out/26"]
FLUXO = {
    "rotulos": MESES,
    "formato": "brl",
    "series": [
        {
            "nome": "Entradas",
            "cor": "entrada",
            "valores": [812000, 812000, 865000, 812000, 930000, 812000],
        },
        {
            "nome": "Saídas",
            "cor": "saida",
            "valores": [634512, 701233, 588790, 742100, 655430, 598870],
        },
    ],
}
PATRIMONIO = {
    "rotulos": MESES,
    "formato": "brl",
    "series": [
        {
            "nome": "Patrimônio",
            "cor": "patrimonio",
            "valores": [4210000, 4388000, 4512500, 4490300, 4733800, 4921000],
        }
    ],
}
LANCAMENTOS = [
    {
        "data": "24/10/2026",
        "descricao": "Mercado",
        "categoria": "Alimentação",
        "slot": 2,
        "valor": -38790,
    },
    {
        "data": "20/10/2026",
        "descricao": "Salário",
        "categoria": "Renda",
        "slot": 3,
        "valor": 812000,
    },
    {
        "data": "18/10/2026",
        "descricao": "Conta de luz",
        "categoria": "Moradia",
        "slot": 1,
        "valor": -21455,
    },
    {
        "data": "15/10/2026",
        "descricao": "Dividendos",
        "categoria": "Proventos",
        "slot": 7,
        "valor": 9312,
    },
]


@router.get("/design")
def design(request: Request):
    return templates.TemplateResponse(
        request,
        "pages/design.html",
        {
            "cores_dados": CORES_DADOS,
            "cores_estado": CORES_ESTADO,
            "superficies": SUPERFICIES,
            "tamanhos": TAMANHOS,
            "fluxo": FLUXO,
            "patrimonio": PATRIMONIO,
            "lancamentos": LANCAMENTOS,
            "variacao_alta": Decimal("4.2"),
            "variacao_baixa": Decimal("-1.8"),
        },
    )


@router.post("/design/toast")
def design_toast():
    """Exemplo do padrão de toast disparado pelo servidor (cabeçalho HX-Trigger)."""
    return Response(
        status_code=204,
        headers=eventos_htmx("Lançamento excluído.", desfazer="/design/desfazer"),
    )


@router.post("/design/desfazer")
def design_desfazer():
    return Response(status_code=204, headers=eventos_htmx("Exclusão desfeita.", tipo="info"))

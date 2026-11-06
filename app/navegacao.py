"""Itens da sidebar, em grupos separados por divisória."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ItemNav:
    rotulo: str
    href: str
    icone: str
    usa_mes: bool = False  # mostra o seletor de mês na topbar


GRUPOS: tuple[tuple[ItemNav, ...], ...] = (
    (
        ItemNav("Visão geral", "/", "layout-dashboard", usa_mes=True),
        ItemNav("Lançamentos", "/lancamentos", "arrow-left-right", usa_mes=True),
        ItemNav("Contas", "/contas", "wallet", usa_mes=True),
        ItemNav("Cartões", "/cartoes", "credit-card", usa_mes=True),
        ItemNav("Orçamento", "/orcamento", "target", usa_mes=True),
        ItemNav("Relatórios", "/relatorios", "table"),
        ItemNav("Categorias", "/categorias", "tags"),
    ),
    (
        ItemNav("Carteira", "/carteira", "briefcase"),
        ItemNav("Proventos", "/proventos", "coins", usa_mes=True),
        ItemNav("Mercado", "/mercado", "trending-up"),
    ),
    (
        ItemNav("Análise", "/analise", "chart-line"),
        ItemNav("Configurações", "/config", "settings"),
        ItemNav("Guia visual", "/design", "palette"),
    ),
)

# Fora da sidebar, mas acessíveis pela busca (Ctrl+K).
SO_NA_BUSCA: tuple[ItemNav, ...] = (ItemNav("Cadastro inicial", "/assistente", "sparkles"),)

ITENS = tuple(item for grupo in GRUPOS for item in grupo)


def item_ativo(caminho: str) -> ItemNav | None:
    """Item da sidebar que corresponde ao caminho (o prefixo mais longo vence)."""
    candidatos = [
        item
        for item in ITENS
        if caminho == item.href or (item.href != "/" and caminho.startswith(item.href + "/"))
    ]
    return max(candidatos, key=lambda item: len(item.href), default=None)

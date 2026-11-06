"""Categorias: um nível de hierarquia, cor por slot da paleta (subcategoria herda do pai)."""

from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Categoria, Desfazer, Lancamento, Recorrencia, TipoCategoria
from app.domain.desfazer import excluir_com_desfazer
from app.domain.formularios import Leitor

# Ícones oferecidos no cadastro (arquivos em static/icons/).
ICONES = (
    "house", "shopping-cart", "utensils", "coffee", "car", "fuel", "bus", "plane",
    "heart-pulse", "pill", "dumbbell", "graduation-cap", "book-open", "party-popper",
    "gamepad-2", "film", "music", "repeat", "smartphone", "wifi", "zap", "droplet",
    "shirt", "scissors", "sparkles", "gift", "baby", "paw-print", "wrench", "receipt",
    "landmark", "percent", "credit-card", "banknote", "piggy-bank", "hand-coins",
    "briefcase-business", "trending-up", "wallet", "circle-ellipsis",
)  # fmt: skip


@dataclass
class Grupo:
    categoria: Categoria
    subcategorias: list[Categoria]


def arvore(sessao: Session, tipo: TipoCategoria, incluir_inativas: bool = False) -> list[Grupo]:
    consulta = (
        select(Categoria)
        .where(Categoria.tipo == tipo, Categoria.pai_id.is_(None))
        .options(selectinload(Categoria.subcategorias))
        .order_by(Categoria.cor_slot == 0, Categoria.nome)  # "Outros" por último
    )
    if not incluir_inativas:
        consulta = consulta.where(Categoria.ativa)
    return [
        Grupo(pai, [f for f in pai.subcategorias if incluir_inativas or f.ativa])
        for pai in sessao.scalars(consulta)
    ]


def opcoes(sessao: Session, tipo: TipoCategoria, manter_id: int | None = None) -> list[tuple]:
    """(id, rótulo) das categorias ativas, subcategorias como "Pai › Filha".

    `manter_id` inclui uma categoria arquivada que já está em uso no lançamento sendo editado.
    """
    resultado = []
    for grupo in arvore(sessao, tipo, incluir_inativas=True):
        for categoria in [grupo.categoria, *grupo.subcategorias]:
            if categoria.ativa or categoria.id == manter_id:
                resultado.append((categoria.id, categoria.nome_completo))
    return resultado


def arquivadas(sessao: Session) -> list[Categoria]:
    return list(sessao.scalars(select(Categoria).where(~Categoria.ativa).order_by(Categoria.nome)))


def uso(sessao: Session, categoria: Categoria) -> int:
    """Quantos lançamentos e recorrências usam a categoria."""
    lancamentos = sessao.scalar(select(func.count()).where(Lancamento.categoria_id == categoria.id))
    recorrencias = sessao.scalar(
        select(func.count()).where(Recorrencia.categoria_id == categoria.id)
    )
    return (lancamentos or 0) + (recorrencias or 0)


def salvar(
    sessao: Session, dados: Mapping[str, str], categoria: Categoria | None = None
) -> Categoria:
    leitor = Leitor(dados)
    nome = leitor.texto("nome", maximo=40)
    tipo = leitor.escolha("tipo", TipoCategoria)
    pai_id = leitor.id("pai_id", obrigatorio=False)
    # Subcategoria herda a cor da principal: o campo nem aparece no formulário.
    cor_slot = leitor.inteiro("cor_slot", 0, 8, obrigatorio=not pai_id)
    icone = leitor.bruto("icone") or "circle-ellipsis"
    if icone not in ICONES:
        leitor.erros["icone"] = "Ícone inválido."

    pai = sessao.get(Categoria, pai_id) if pai_id else None
    if pai_id and pai is None:
        leitor.erros["pai_id"] = "Categoria principal não encontrada."
    if pai is not None:
        if pai.pai_id is not None:
            leitor.erros["pai_id"] = "Só é possível um nível de subcategoria."
        elif categoria is not None and pai.id == categoria.id:
            leitor.erros["pai_id"] = "Uma categoria não pode ser subcategoria dela mesma."
        elif tipo and pai.tipo != tipo:
            leitor.erros["pai_id"] = "A categoria principal precisa ser do mesmo tipo."
        elif categoria is not None and categoria.subcategorias:
            leitor.erros["pai_id"] = "Esta categoria tem subcategorias e não pode virar uma."

    if categoria is not None and tipo and tipo != categoria.tipo and uso(sessao, categoria):
        leitor.erros["tipo"] = "Não dá para mudar o tipo de uma categoria que já tem lançamentos."

    if nome and tipo:
        repetida = sessao.scalar(
            select(Categoria.id).where(
                func.lower(Categoria.nome) == nome.lower(),
                Categoria.tipo == tipo,
                Categoria.pai_id.is_(None) if pai is None else Categoria.pai_id == pai.id,
                Categoria.id != (categoria.id if categoria else -1),
            )
        )
        if repetida:
            leitor.erros["nome"] = "Já existe uma categoria com esse nome aqui."
    leitor.verificar()

    if categoria is None:
        categoria = Categoria()
        sessao.add(categoria)
    categoria.nome = nome
    categoria.tipo = tipo
    categoria.pai = pai
    categoria.icone = icone
    # A cor identifica a categoria principal nos gráficos; subcategorias herdam.
    categoria.cor_slot = pai.cor_slot if pai else cor_slot
    for filha in categoria.subcategorias:
        filha.cor_slot = categoria.cor_slot
        filha.tipo = categoria.tipo
    sessao.flush()
    return categoria


def excluir(sessao: Session, categoria: Categoria) -> Desfazer | None:
    """Exclui se nunca foi usada (com Desfazer). Se foi usada ou tem subcategorias, arquiva
    (junto com as subcategorias) para não quebrar o histórico. Devolve o registro ou None."""
    if categoria.subcategorias or uso(sessao, categoria):
        for item in [categoria, *categoria.subcategorias]:
            item.ativa = False
        sessao.flush()
        return None
    return excluir_com_desfazer(sessao, [categoria], f"Categoria “{categoria.nome}”")


def reativar(sessao: Session, categoria: Categoria) -> None:
    categoria.ativa = True
    if categoria.pai is not None:
        categoria.pai.ativa = True
    sessao.flush()

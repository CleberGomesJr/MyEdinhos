"""Snapshot para análise com o Claude (E6-07) e modelo de pergunta (E6-08).

Gera Markdown (para colar numa conversa) e um dicionário (JSON). O app não envia nada:
o usuário decide onde usar. Sempre com data e fonte das cotações.
"""

from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.base import agora_utc
from app.domain import alocacao, carteira, dividendos, mercado, objetivos, proventos, regras
from app.domain import rentabilidade as rent
from app.domain.dinheiro import formatar_brl, formatar_percentual, formatar_preco
from app.domain.operacoes import formatar_quantidade

AVISO = "Apoio à decisão, não recomendação de investimento. A decisão é do investidor."


def _pct(valor: Decimal | None) -> str:
    return formatar_percentual(valor) if valor is not None else "—"


def _num(valor):
    if isinstance(valor, Decimal):
        return format(valor, "f")
    if isinstance(valor, date | datetime):
        return valor.isoformat()
    return valor


def montar(sessao: Session, hoje: date) -> dict:
    cart = carteira.montar(sessao, hoje)
    situacao = alocacao.alocacao(sessao, hoje, cart)
    reserva = alocacao.reserva(sessao, hoje, cart)
    por_unidade = proventos.proventos_por_unidade_12m(sessao, hoje)
    indicadores = dividendos.indicadores(sessao, [ln.ativo for ln in cart.linhas], hoje)
    rentab = rent.calcular(sessao, hoje)
    status = mercado.status(sessao)
    indices = mercado.indices(sessao, hoje)
    meta = proventos.meta(sessao, cart.proventos_12m, cart.yield_on_cost)
    base = situacao.total
    posicoes = []
    for linha in cart.linhas:
        ind = indicadores[linha.ativo.id]
        posicoes.append(
            {
                "ticker": linha.ativo.ticker,
                "nome": linha.ativo.nome,
                "classe": linha.ativo.classe.rotulo,
                "setor": linha.ativo.setor,
                "quantidade": linha.posicao.quantidade,
                "preco_medio": linha.posicao.preco_medio,
                "custo": linha.posicao.custo_centavos,
                "valor": linha.valor,
                "valor_origem": linha.origem,
                "cotacao": linha.cotacao.fechamento if linha.cotacao else None,
                "cotacao_data": linha.cotacao.data if linha.cotacao else None,
                "cotacao_fonte": linha.cotacao.fonte if linha.cotacao else None,
                "participacao_pct": linha.participacao,
                "resultado_pct": linha.resultado_pct,
                "proventos_12m": linha.proventos_12m,
                "yield_on_cost_pct": linha.yield_on_cost,
                "dividend_yield_pct": proventos.dividend_yield(linha, por_unidade),
                "frequencia_proventos": ind.frequencia if ind.tem_historico else None,
                "anos_seguidos_pagando": ind.anos_seguidos(hoje.year)
                if ind.tem_historico
                else None,
                "anos_com_corte": ind.cortes(hoje.year),
                "reserva_emergencia": linha.ativo.reserva_emergencia,
            }
        )
    return {
        "gerado_em": agora_utc(),
        "data_referencia": hoje,
        "aviso": AVISO,
        "cotacoes_atualizadas_em": status.em,
        "resumo": {
            "patrimonio_investido": cart.patrimonio,
            "aportado": cart.aportado,
            "resultado_nao_realizado": cart.resultado,
            "resultado_realizado_vendas": cart.realizado,
            "proventos_12m": cart.proventos_12m,
            "yield_on_cost_pct": cart.yield_on_cost,
            "patrimonio_com_reserva": base,
            "rentabilidade_pct": rentab.retorno,
            "rentabilidade_base": "ao ano"
            if rentab.anualizado
            else f"no período de {rentab.dias} dias",
            "comparacoes": {c.nome: c.retorno for c in rentab.comparacoes},
        },
        "alocacao": [
            {
                "grupo": ln.grupo.rotulo,
                "valor": ln.valor,
                "atual_pct": ln.percentual,
                "alvo_pct": ln.alvo,
                "tolerancia_pp": ln.tolerancia,
                "estado": ln.estado,
            }
            for ln in situacao.linhas
        ],  # fmt: skip
        "reserva_emergencia": {
            "meses": reserva.meses,
            "despesa_media_mensal": reserva.despesa_media,
            "alvo": reserva.alvo,
            "atual": reserva.atual,
            "completa": reserva.completa,
        },  # fmt: skip
        "renda_passiva": {"meta_mensal": meta.mensal, "media_12m": meta.media_12m}
        if meta
        else None,
        "premissas": asdict(objetivos.premissas(sessao)),
        "regras": asdict(regras.obter(sessao)),
        "alertas": [asdict(a) for a in regras.alertas(sessao, hoje, cart)],
        "objetivos": [
            {
                "nome": p.nome,
                "alvo": p.alvo,
                "prazo": p.prazo,
                "atual": p.atual,
                "meses_estimados": p.meses_estimados,
            }
            for p in objetivos.progresso(sessao, hoje, cart, base)
        ],  # fmt: skip
        "indices": {
            "selic_meta_aa_pct": indices.selic_meta,
            "cdi_12m_pct": indices.cdi_12m,
            "ipca_12m_pct": indices.ipca_12m,
        },  # fmt: skip
        "posicoes": posicoes,
    }


def para_json(dados) -> object:
    """Decimal → texto, datas → ISO; o resto como está (valores em centavos)."""
    if isinstance(dados, dict):
        return {chave: para_json(valor) for chave, valor in dados.items()}
    if isinstance(dados, list):
        return [para_json(item) for item in dados]
    return _num(dados)


def para_markdown(dados: dict) -> str:
    r = dados["resumo"]
    linhas = [
        f"# Snapshot da carteira — {dados['data_referencia']:%d/%m/%Y}",
        "",
        f"> {dados['aviso']}",
        "",
        "Valores em reais. Cotações: "
        + (f"atualizadas em {dados['cotacoes_atualizadas_em']:%d/%m/%Y %H:%M} UTC; "
           if dados["cotacoes_atualizadas_em"] else "ainda não atualizadas; ")
        + "data e fonte de cada uma na tabela de posições.",
        "",
        "## Resumo",
        f"- Patrimônio investido: {formatar_brl(r['patrimonio_investido'])} "
        f"(aportado {formatar_brl(r['aportado'])}; resultado {formatar_brl(r['resultado_nao_realizado'], sinal=True)})",
        f"- Com a reserva em conta: {formatar_brl(r['patrimonio_com_reserva'])}",
        f"- Rentabilidade (XIRR) {r['rentabilidade_base']}: {_pct(r['rentabilidade_pct'])} · "
        + " · ".join(f"{nome} {_pct(valor)}" for nome, valor in r["comparacoes"].items()),
        f"- Proventos em 12 meses: {formatar_brl(r['proventos_12m'])} (yield on cost {_pct(r['yield_on_cost_pct'])})",
    ]  # fmt: skip
    if dados["renda_passiva"]:
        rp = dados["renda_passiva"]
        linhas.append(f"- Meta de renda passiva: {formatar_brl(rp['meta_mensal'])}/mês; média de 12 meses {formatar_brl(rp['media_12m'])}")  # fmt: skip
    res = dados["reserva_emergencia"]
    if res["alvo"]:
        linhas.append(
            f"- Reserva de emergência: {formatar_brl(res['atual'])} de {formatar_brl(res['alvo'])} "
            f"({res['meses']} meses × {formatar_brl(res['despesa_media_mensal'])}) — "
            + ("completa" if res["completa"] else "incompleta")
        )
    linhas += ["", "## Alocação (atual × alvo)", "| Grupo | Valor | Atual | Alvo | Tolerância | Situação |",
               "|---|---:|---:|---:|---:|---|"]  # fmt: skip
    for a in dados["alocacao"]:
        linhas.append(
            f"| {a['grupo']} | {formatar_brl(a['valor'])} | {_pct(a['atual_pct'])} | {_pct(a['alvo_pct'])} | "
            f"{'± ' + format(a['tolerancia_pp'].normalize(), 'f') + ' pp' if a['tolerancia_pp'] is not None else '—'} | {a['estado'] or '—'} |"
        )  # fmt: skip
    linhas += ["", "## Posições",
               "| Ativo | Classe | Setor | Qtd | Preço médio | Cotação (data, fonte) | Valor | % carteira | Resultado | DY 12m | YoC | Proventos 12m | Frequência | Anos pagando | Cortes |",
               "|---|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---|---:|---|"]  # fmt: skip
    for p in dados["posicoes"]:
        cotacao = (f"{formatar_preco(p['cotacao'])} ({p['cotacao_data']:%d/%m}, {p['cotacao_fonte']})"
                   if p["cotacao"] is not None else {"estimativa": "estimado pelo indexador", "custo": "sem cotação (custo)"}.get(p["valor_origem"], "—"))  # fmt: skip
        linhas.append(
            f"| {p['ticker']} | {p['classe']} | {p['setor'] or '—'} | {formatar_quantidade(p['quantidade'])} | "
            f"{formatar_preco(p['preco_medio'])} | {cotacao} | {formatar_brl(p['valor'])} | {_pct(p['participacao_pct'])} | "
            f"{_pct(p['resultado_pct'])} | {_pct(p['dividend_yield_pct'])} | {_pct(p['yield_on_cost_pct'])} | "
            f"{formatar_brl(p['proventos_12m'])} | {p['frequencia_proventos'] or '—'} | "
            f"{p['anos_seguidos_pagando'] if p['anos_seguidos_pagando'] is not None else '—'} | "
            f"{', '.join(map(str, p['anos_com_corte'])) or '—'} |"
        )  # fmt: skip
    rg = dados["regras"]
    linhas += ["", "## Regras pessoais configuradas no app"]
    descricoes = {
        "limite_ativo_pct": "Limite por ativo (ações e FIIs), % da carteira",
        "limite_etf_pct": "Limite por ETF/BDR, %",
        "transicao_patrimonio": "Regra de transição abaixo de (R$)",
        "transicao_limite_pct": "Limite por ativo na transição, %",
        "transicao_max_por_setor": "Máximo de ações do mesmo setor na transição",
        "limite_setor_pct": "Limite por setor (ações), %",
        "limite_tipo_fii_pct": "Limite por tipo de FII (tijolo/papel), %",
        "papel_max_pct_fiis": "FIIs de papel, máximo da parcela de FIIs, %",
        "cripto_teto_pct": "Teto de cripto, %",
        "dy_min_acoes": "DY mínimo de ações, %",
        "dy_min_fiis": "DY mínimo de FIIs, %",
        "setores_excluidos": "Setores excluídos",
        "reserva_primeiro": "Aporte vai primeiro para a reserva",
    }
    for chave, rotulo in descricoes.items():
        valor = rg[chave]
        if chave == "transicao_patrimonio" and valor:
            valor = formatar_brl(valor)
        elif isinstance(valor, list):
            valor = ", ".join(valor) or "—"
        elif isinstance(valor, bool):
            valor = "sim" if valor else "não"
        elif isinstance(valor, Decimal):
            valor = format(valor.normalize(), "f")
        linhas.append(f"- {rotulo}: {valor if valor not in (None, '') else '—'}")
    linhas += ["", "## Alertas atuais"]
    linhas += [f"- [{a['nivel']}] {a['titulo']}: {a['detalhe']}" for a in dados["alertas"]] or [
        "- Nenhum."
    ]
    if dados["objetivos"]:
        linhas += ["", "## Objetivos"]
        for o in dados["objetivos"]:
            prazo = f" até {o['prazo']:%m/%Y}" if o["prazo"] else ""
            estimado = (
                f"; no ritmo atual, ~{o['meses_estimados']} meses"
                if o["meses_estimados"] is not None
                else ""
            )
            linhas.append(f"- {o['nome']}: {formatar_brl(o['alvo'])}{prazo}{estimado}")
    p = dados["premissas"]
    i = dados["indices"]
    linhas += [
        "", "## Premissas e índices",
        f"- Premissas das projeções: retorno {_pct(p['retorno_aa'])} ao ano, yield {_pct(p['yield_aa'])}, "
        f"aporte mensal {formatar_brl(p['aporte_mensal'])}",
        f"- Selic meta {_pct(i['selic_meta_aa_pct'])} a.a. · CDI 12 meses {_pct(i['cdi_12m_pct'])} · IPCA 12 meses {_pct(i['ipca_12m_pct'])} (Banco Central)",
        "",
    ]  # fmt: skip
    return "\n".join(linhas)


ACOES = {"comprar": "comprar", "vender": "vender", "aportar": "aportar mais em", "manter": "manter"}


def pergunta(dados: dict, ticker: str, acao: str, valor: int | None = None) -> str:
    """E6-08: pergunta pronta para colar numa conversa com o Claude, com o snapshot anexado."""
    verbo = ACOES.get(acao, "analisar")
    alvo = f"{verbo} {ticker.upper()}" + (f" (cerca de {formatar_brl(valor)})" if valor else "")
    na_carteira = any(p["ticker"] == ticker.upper() for p in dados["posicoes"])
    return "\n".join([
        f"Estou pensando em {alvo}. {'Já tenho esse ativo.' if na_carteira else 'Ainda não tenho esse ativo.'}",
        "",
        "Analise com base nas regras, na alocação-alvo, nos objetivos e nos dados abaixo. Quero:",
        "1. Se a decisão respeita as regras configuradas (limite por ativo e por setor, regra de transição, "
        "setores excluídos, reserva de emergência antes de renda variável) e a alocação-alvo.",
        "2. Os fundamentos do ativo: para ações, lucro, ROE, margem, endividamento, dividend yield, payout e "
        "histórico de dividendos; para FIIs, dividend yield, P/VP, vacância, liquidez e cortes recentes.",
        "3. Prós, contras e riscos (consistência dos dividendos, concentração, valuation), dizendo quais dados "
        "você não tem e onde eu confiro.",
        "4. Uma conclusão objetiva (faz sentido / não faz / depende de quê), lembrando que a decisão é minha.",
        "",
        "---",
        "",
        para_markdown(dados),
    ])  # fmt: skip

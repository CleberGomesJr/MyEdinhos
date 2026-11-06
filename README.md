# MyEdinhos

Controle financeiro e de investimentos pessoal que roda **só no seu computador**. Feito em Python (FastAPI + SQLite), com interface web escura e minimalista aberta no navegador. Os dados ficam num único arquivo em `data/` e nunca saem da máquina; a internet só é usada para buscar cotações e índices (vão apenas os códigos dos ativos).

## Funções

**Finanças do mês**

- **Visão geral** — saldo, entradas, saídas, resultado e taxa de poupança do mês, com variação; gráficos de entradas × saídas (12 meses), despesas por categoria e evolução do saldo; contas a pagar nos próximos 7 dias e uso do limite dos cartões.
- **Lançamentos** — entradas, saídas e transferências, com pago/pendente, vencimento, atrasados, parcelamentos, recorrências (mensal, semanal, anual), filtros, exportação em CSV e **Desfazer** ao excluir; **importação de extrato** CSV/OFX do banco, com revisão e sem duplicar.
- **Contas** — saldo de hoje e previsto do mês (já descontando faturas a vencer), conta marcável como reserva de emergência.
- **Cartões** — cartões com limite, fechamento e vencimento; compras à vista, parceladas e assinaturas caem na fatura certa; pagamento total ou parcial, estornos e limite disponível.
- **Orçamento** — limite por categoria a partir de um mês, com estados dentro / perto / estourou.
- **Relatórios** — resumo anual e mapa de calor categoria × mês; alternância entre regime de competência e de caixa.
- **Categorias** — categorias e subcategorias com cor e ícone (já vem com um conjunto padrão).
- **Cadastro inicial** — assistente em 3 passos (contas e saldos, faturas e parcelamentos em aberto, carteira atual).

**Investimentos**

- **Carteira** — ações, FIIs, ETFs, BDRs, renda fixa/Tesouro e cripto; compras, vendas, desdobramentos e bonificações; preço médio, resultado realizado e não realizado, alocação e evolução do patrimônio; **importação de operações** do CSV da corretora ou da B3.
- **Proventos** — dividendos, JCP e rendimentos recebidos e a receber, *yield on cost*, *dividend yield*, consistência dos pagamentos e meta de renda passiva mensal.
- **Mercado** — Selic, CDI e IPCA (Banco Central) e cotações dos ativos acompanhados, atualizados automaticamente 1× por dia, sempre com data e fonte; preços e taxas do **Tesouro Direto** (dados abertos do Tesouro Transparente).

**Análise**

- Rentabilidade da carteira (XIRR) comparada com CDI, IPCA e Ibovespa.
- Alocação-alvo por classe com tolerância e **sugestão de onde colocar o próximo aporte** (reserva de emergência primeiro).
- Regras pessoais (limite por ativo/setor, DY mínimo, setores excluídos…) que geram alertas de "fora da regra" e de corte/suspensão de dividendos.
- Objetivos com valor e prazo, reserva de emergência e projeção da renda passiva com premissas editáveis.
- **Snapshot** da carteira em Markdown/JSON e pergunta pronta para analisar a compra ou venda de um ativo com o Claude (o app não envia nada; você copia).

**Outros**

- **Configurações** — exportar e importar tudo (.zip), backups e informações dos dados.
- Backup automático do banco a cada início (guarda os 14 mais recentes), migrações automáticas e logs rotativos.
- Atalhos de teclado (`N` novo lançamento, `←`/`→` troca o mês, `Ctrl+K` busca de páginas) e layout que funciona no celular.

> Apoio à decisão, não recomendação de investimento.

## Como rodar

Pré-requisitos: **Python 3.12+** e o gerenciador **[uv](https://docs.astral.sh/uv/)**.

```bash
# 1. Instalar o uv (se ainda não tiver)
pip install uv

# 2. Entrar na pasta do projeto (extraída ou clonada)
cd MyEdinhos

# 3. Instalar as dependências
uv sync

# 4. (Opcional) Criar o arquivo de configuração
cp .env.example .env        # Windows: copy .env.example .env

# 5. Iniciar: cria o banco, aplica as migrações, faz backup e abre o navegador
uv run iniciar
```

O app abre em **http://127.0.0.1:8000** totalmente vazio, como de fábrica (só com as categorias padrão): comece pelo **Cadastro inicial** (`Ctrl+K` → "Cadastro inicial") ou pela tela **Contas**. No Windows, basta dar dois cliques em `iniciar.bat`. Se o `uv` não estiver no PATH, use `python -m uv run ...`.

Outros comandos:

| Comando | O que faz |
|---|---|
| `uv run iniciar` | O mesmo que o `iniciar.bat` |
| `uv run atalho` | Cria o atalho na Área de Trabalho (Windows) |
| `uv run demo` | Abre a demonstração com dados fictícios |
| `uv run seed-demo` | Recria os dados da demonstração |
| `uv run dev` | Servidor de desenvolvimento (recarrega ao salvar código) |
| `uv run migrate` | Backup + aplica migrações pendentes |
| `uv run check` | ruff (lint e formatação) + pytest |
| `uv run e2e` | Testes no navegador real (Edge) e regressão visual do tema escuro |
| `uv run alembic revision --autogenerate -m "descricao"` | Cria uma migração a partir dos modelos |

Se o `uv` não estiver no PATH, use `python -m uv run ...`.

Configuração opcional no `.env`: porta (`MYEDINHOS_PORTA`), pasta de dados (`MYEDINHOS_DATA_DIR`), nível de log (`MYEDINHOS_NIVEL_LOG`) e `BRAPI_TOKEN` (token gratuito da [brapi.dev](https://brapi.dev) — sem ele as cotações vêm do Yahoo). Os dados ficam em `data/` (`financas.db`, `backups/`, `logs/`); para restaurar um backup, feche o app e copie o arquivo de `data/backups/` por cima de `data/financas.db`. A pasta `data/` e o `.env` nunca vão para o git.

## Ver sem usar seus dados (demonstração)

`uv run demo` abre o app em outra porta com 12 meses fictícios (`uv run seed-demo` recria esses dados). Fica em `data/demo/`, em banco separado: nada toca nos seus dados reais, e há um aviso no topo de toda tela.

## Dados, backup e restauração

- Banco: `data/financas.db` (SQLite). A pasta `data/` e o `.env` nunca vão para o git.
- **Backups automáticos**: `data/backups/AAAA-MM-DD_HHMM.db`, a cada início e antes de migrar; ficam os 14 mais recentes. Cópias com outro nome nessa pasta não são apagadas.
- **Levar para fora do computador**: Configurações → **Exportar tudo** gera um `.zip` com o banco e uma planilha CSV por tabela. Guarde em lugar seguro: tem todos os seus dados financeiros.
- **Restaurar**: Configurações → **Importar**, escolhendo o `.zip` exportado ou um `.db` da pasta de backups. O arquivo é conferido antes e o banco atual fica guardado como `antes-de-importar_*.db`. Sem abrir o app: feche-o, copie o backup por cima de `data/financas.db` e apague `financas.db-wal` e `financas.db-shm`, se existirem.
- Logs: `data/logs/app.log` (rotativo).

## Segurança

O app só escuta em `127.0.0.1`, não tem login e só acessa a internet para cotações e índices (código em `app/integrations/`); nada sobre você é enviado. Outros sites abertos no navegador não conseguem enviar comandos a ele nem ler seus dados.

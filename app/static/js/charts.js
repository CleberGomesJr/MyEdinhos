/* Tema do Chart.js a partir dos tokens CSS (vault "04 - Design System").
   Uso: macro `grafico` em components/ui.html — <canvas data-grafico="barras|linha" data-dados="id-do-json">.
   Valores monetários chegam em centavos (inteiros); só viram reais na formatação para exibir. */
(() => {
  "use strict";

  const css = (nome) => getComputedStyle(document.documentElement).getPropertyValue(nome).trim();

  // Papel do dado → token. Nunca criar cor fora destes.
  const PAPEIS = {
    entrada: "--entrada", saida: "--saida", patrimonio: "--patrimonio", outros: "--s-outros",
    s1: "--s1", s2: "--s2", s3: "--s3", s4: "--s4", s5: "--s5", s6: "--s6", s7: "--s7", s8: "--s8",
  };
  const cor = (papel) => css(PAPEIS[papel] || "--s-outros");

  const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
  const brlCompacto = new Intl.NumberFormat("pt-BR", {
    style: "currency", currency: "BRL", notation: "compact", maximumFractionDigits: 1,
  });
  const numero = new Intl.NumberFormat("pt-BR");

  // Centavos → texto. A divisão é só para exibir; nenhum cálculo é feito com o resultado.
  const formatar = (valor, formato, compacto = false) =>
    formato === "brl" ? (compacto ? brlCompacto : brl).format(valor / 100) : numero.format(valor);

  const reduzirMovimento = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function aplicarTema() {
    const padrao = Chart.defaults;
    padrao.font.family = css("--fonte");
    padrao.font.size = 12;
    padrao.color = css("--text-3");
    padrao.borderColor = css("--grid");
    padrao.maintainAspectRatio = false;
    padrao.animation = reduzirMovimento ? false : { duration: 300 };
    padrao.interaction = { mode: "index", intersect: false };

    const legenda = padrao.plugins.legend;
    legenda.align = "start";
    legenda.labels.color = css("--text-2");
    legenda.labels.usePointStyle = true;
    legenda.labels.pointStyle = "rectRounded";
    legenda.labels.boxWidth = 8;
    legenda.labels.boxHeight = 8;
    legenda.labels.padding = 16;

    const dica = padrao.plugins.tooltip;
    dica.backgroundColor = css("--surface-3");
    dica.borderColor = css("--border-strong");
    dica.borderWidth = 1;
    dica.titleColor = css("--text");
    dica.bodyColor = css("--text-2");
    dica.padding = 10;
    dica.cornerRadius = 6;
    dica.boxPadding = 4;
    dica.usePointStyle = true;
  }

  // Barras partem do zero; linhas (saldo) acompanham a faixa dos dados.
  function eixos(formato, tipo) {
    return {
      x: {
        grid: { display: false },
        border: { color: css("--axis") },
        // Séries longas (cotação diária): no máximo 12 rótulos, sem inclinar.
        ticks: { color: css("--text-3"), autoSkip: true, maxTicksLimit: 12, maxRotation: 0 },
      },
      y: {
        beginAtZero: tipo === "barras",
        grace: tipo === "barras" ? 0 : "5%",
        grid: { color: css("--grid") },
        border: { display: false },
        ticks: { color: css("--text-3"), callback: (valor) => formatar(valor, formato, true) },
      },
    };
  }

  const CONJUNTOS = {
    barras: (serie) => ({
      backgroundColor: cor(serie.cor),
      borderRadius: { topLeft: 4, topRight: 4 },
      borderSkipped: "start",
      maxBarThickness: 16,
      categoryPercentage: 0.6,
      barPercentage: 0.85,
    }),
    linha: (serie) => ({
      borderColor: cor(serie.cor),
      backgroundColor: cor(serie.cor),
      borderWidth: 2,
      // Marcador de 8 px; em séries longas (saldo diário) só aparece ao passar o mouse.
      pointRadius: serie.valores.length > 16 ? 0 : 4,
      pointHoverRadius: 5,
      tension: 0.25,
    }),
  };

  function criar(canvas) {
    const tipo = canvas.dataset.grafico;
    const dados = JSON.parse(document.getElementById(canvas.dataset.dados).textContent);
    const conjunto = CONJUNTOS[tipo];
    if (!conjunto) throw new Error(`Tipo de gráfico desconhecido: ${tipo}`);

    return new Chart(canvas, {
      type: tipo === "barras" ? "bar" : "line",
      data: {
        labels: dados.rotulos,
        datasets: dados.series.map((serie) => ({
          label: serie.nome,
          data: serie.valores,
          ...conjunto(serie),
        })),
      },
      options: {
        scales: eixos(dados.formato, tipo),
        plugins: {
          legend: { display: dados.series.length >= 2 },
          tooltip: {
            callbacks: {
              label: (item) => ` ${item.dataset.label}: ${formatar(item.parsed.y, dados.formato)}`,
            },
          },
        },
      },
    });
  }

  function iniciar(raiz = document) {
    raiz.querySelectorAll("canvas[data-grafico]").forEach((canvas) => {
      if (!Chart.getChart(canvas)) criar(canvas);
    });
  }

  // Espera a Inter carregar para o canvas não desenhar com a fonte de fallback.
  document.fonts.ready.then(() => {
    aplicarTema();
    iniciar();
    document.addEventListener("htmx:load", (evento) => iniciar(evento.detail.elt));
  });

  window.Graficos = { cor, formatar, criar, iniciar };
})();

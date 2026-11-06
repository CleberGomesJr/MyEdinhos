/* MyEdinhos — comportamento da interface. JS puro, sem build.
   Sidebar · drawer/modal/HTMX · toast · campo de moeda · atalhos · busca (Ctrl+K). */
(() => {
  "use strict";

  const $ = (seletor, raiz = document) => raiz.querySelector(seletor);
  const $$ = (seletor, raiz = document) => [...raiz.querySelectorAll(seletor)];
  const html = document.documentElement;

  /* ───────── Sidebar (recolher no desktop, menu no celular) ───────── */
  const recolher = $("#recolher-sidebar");
  recolher?.addEventListener("click", () => {
    const recolhida = html.classList.toggle("sidebar-recolhida");
    recolher.setAttribute("aria-expanded", String(!recolhida));
    document.cookie = `sidebar=${recolhida ? "recolhida" : "aberta"}; path=/; max-age=31536000; samesite=lax`;
  });

  const abrirMenu = $("#abrir-menu");
  const fecharMenu = () => {
    html.classList.remove("menu-aberto");
    abrirMenu?.setAttribute("aria-expanded", "false");
  };
  abrirMenu?.addEventListener("click", () => {
    const aberto = html.classList.toggle("menu-aberto");
    abrirMenu.setAttribute("aria-expanded", String(aberto));
    if (aberto) $("#sidebar .nav-item")?.focus();
  });
  $$("[data-fechar-menu]").forEach((el) => el.addEventListener("click", fecharMenu));

  /* ───────── Diálogos ───────── */
  // [data-fechar] fecha o diálogo em que está; clique fora da caixa (no fundo) também fecha.
  document.addEventListener("click", (evento) => {
    evento.target.closest("[data-fechar]")?.closest("dialog")?.close();
  });
  $$("dialog").forEach((dialogo) => {
    dialogo.addEventListener("click", (evento) => {
      if (evento.target !== dialogo) return;
      const caixa = dialogo.getBoundingClientRect();
      const dentro =
        evento.clientX >= caixa.left && evento.clientX <= caixa.right &&
        evento.clientY >= caixa.top && evento.clientY <= caixa.bottom;
      if (!dentro) dialogo.close();
    });
  });

  // Drawer: conteúdo carregado por HTMX em #drawer-conteudo; abre já com skeleton.
  const drawer = $("#drawer");
  const drawerConteudo = $("#drawer-conteudo");
  const abrirDrawer = () => {
    if (!drawer.open) drawer.showModal();
  };
  document.addEventListener("htmx:beforeRequest", (evento) => {
    // Só ao abrir: com o drawer aberto (envio do formulário), trocar o conteúdo tiraria o
    // formulário da página e os eventos da resposta (toast, fechar) se perderiam.
    if (evento.detail.target !== drawerConteudo || drawer.open) return;
    drawerConteudo.replaceChildren($("#modelo-carregando").content.cloneNode(true));
    abrirDrawer();
  });
  document.addEventListener("htmx:afterSwap", (evento) => {
    if (evento.detail.target === drawerConteudo) abrirDrawer();
  });
  // O foco só depois de assentar: o HTMX também foca o [autofocus] nessa hora. Com foco antes
  // (no afterSwap), ele voltava ao 1º campo no meio da digitação e o texto caía no campo errado.
  document.addEventListener("htmx:afterSettle", (evento) => {
    if (evento.detail.target !== drawerConteudo) return;
    const foco =
      $("[aria-invalid=true]", drawerConteudo) ||
      $("[autofocus]", drawerConteudo) ||
      $("input:not([type=hidden]):not(.sr-only), select, textarea", drawerConteudo) ||
      $("[data-fechar]", drawerConteudo);
    foco?.focus();
  });
  drawer.addEventListener("close", () => drawerConteudo.replaceChildren());
  // Teclado nos formulários do drawer: Enter num select também salva (o navegador não faz
  // isso sozinho) e Ctrl+Enter salva de qualquer campo.
  drawer.addEventListener("keydown", (evento) => {
    const formulario = evento.target.form;
    if (!formulario || evento.key !== "Enter") return;
    if (evento.ctrlKey || evento.metaKey || evento.target.tagName === "SELECT") {
      evento.preventDefault();
      formulario.requestSubmit();
    }
  });
  // O servidor fecha o drawer ao concluir uma ação (HX-Trigger "fechar-drawer").
  document.body.addEventListener("fechar-drawer", () => drawer.open && drawer.close());

  /* ───────── Campos condicionais ───────── */
  // <div data-mostrar-se="tipo=saida|entrada"> só aparece (e só envia seus campos) quando o
  // campo "tipo" do formulário vale saida ou entrada. Checkbox vale "sim" ou "nao".
  function valorDoCampo(formulario, nome) {
    const campos = $$(`[name="${nome}"]`, formulario);
    // Campos dentro de um bloco escondido não valem (ex.: "Pagar com" numa transferência).
    const radio = campos.find(
      (campo) => campo.type === "radio" && campo.checked && !campo.closest("[hidden]")
    );
    if (radio) return radio.value;
    const campo = campos.find((item) => !item.closest("[hidden]"));
    if (!campo) return "";
    if (campo.type === "checkbox") return campo.checked ? "sim" : "nao";
    return campo.value;
  }

  function aplicarCondicionais(formulario) {
    // Duas passadas: um bloco pode depender de um campo que outro bloco acabou de mostrar.
    for (let passada = 0; passada < 2; passada++) {
      $$("[data-mostrar-se]", formulario).forEach((bloco) => {
        const [nome, valores] = bloco.dataset.mostrarSe.split("=");
        const mostrar = valores.split("|").includes(valorDoCampo(formulario, nome));
        bloco.hidden = !mostrar;
        $$("input, select, textarea", bloco).forEach((campo) => (campo.disabled = !mostrar));
      });
    }
  }

  document.addEventListener("change", (evento) => {
    const formulario = evento.target.form;
    if (formulario?.querySelector("[data-mostrar-se]")) aplicarCondicionais(formulario);
  });
  htmx.onLoad((raiz) => {
    const formularios = raiz.matches?.("form") ? [raiz] : $$("form", raiz);
    formularios.forEach((formulario) => {
      if (formulario.querySelector("[data-mostrar-se]")) aplicarCondicionais(formulario);
    });
  });

  // Modal de confirmação: substitui o confirm() do navegador, inclusive no hx-confirm.
  const modal = $("#confirmacao");
  $$("button[value]", modal).forEach((botao) =>
    botao.addEventListener("click", () => modal.close(botao.value))
  );

  function confirmar(mensagem, { titulo = "Confirmar", acao = "Confirmar" } = {}) {
    $("#confirmacao-titulo").textContent = titulo;
    $("#confirmacao-texto").textContent = mensagem;
    $("#confirmacao-ok").textContent = acao;
    modal.returnValue = "";
    modal.showModal();
    $('button[value="nao"]', modal).focus();
    return new Promise((resolver) =>
      modal.addEventListener("close", () => resolver(modal.returnValue === "sim"), { once: true })
    );
  }

  document.addEventListener("htmx:confirm", (evento) => {
    if (!evento.detail.question) return;
    evento.preventDefault();
    const { confirmarTitulo, confirmarAcao } = evento.detail.elt.dataset;
    confirmar(evento.detail.question, { titulo: confirmarTitulo, acao: confirmarAcao }).then(
      (ok) => ok && evento.detail.issueRequest(true)
    );
  });

  /* ───────── Toast ───────── */
  // Do servidor: cabeçalho HX-Trigger {"toast": {mensagem, tipo, desfazer}} (app/web.py).
  const toasts = $("#toasts");

  function mostrarToast({ mensagem, tipo = "ok", desfazer = null }) {
    const toast = $("#modelo-toast").content.firstElementChild.cloneNode(true);
    toast.classList.add(`toast-${tipo}`);
    $$(".toast-icone", toast).forEach((icone) => icone.dataset.tipo !== tipo && icone.remove());
    $(".toast-texto", toast).textContent = mensagem;

    let temporizador;
    const fechar = () => {
      clearTimeout(temporizador);
      toast.remove();
    };
    const agendar = (ms) => {
      clearTimeout(temporizador);
      temporizador = setTimeout(fechar, ms);
    };

    if (desfazer) {
      const botao = $(".toast-desfazer", toast);
      botao.hidden = false;
      botao.addEventListener("click", () => {
        fechar();
        htmx.ajax("POST", desfazer, { swap: "none" });
      });
    }
    $(".toast-fechar", toast).addEventListener("click", fechar);
    toast.addEventListener("mouseenter", () => clearTimeout(temporizador));
    toast.addEventListener("mouseleave", () => agendar(3000));

    toasts.append(toast);
    agendar(desfazer ? 8000 : 5000);
  }

  document.body.addEventListener("toast", (evento) => mostrarToast(evento.detail));

  // Preenche os campos com o valor de data-sugestao (ex.: alocação do perfil moderado).
  document.addEventListener("click", (evento) => {
    const botao = evento.target.closest("[data-preencher-sugestao]");
    if (!botao) return;
    $$("[data-sugestao]", botao.form).forEach((campo) => (campo.value = campo.dataset.sugestao));
  });

  // Importação (E2-13, E4-11): marca ou desmarca todas as linhas da revisão.
  document.addEventListener("change", (evento) => {
    const mestre = evento.target.closest("[data-marcar-todas]");
    if (!mestre) return;
    for (const caixa of mestre.closest("form").querySelectorAll('input[name^="importar-"]:not(:disabled)')) {
      caixa.checked = mestre.checked;
    }
  });

  // Copiar texto (E6-07/E6-08): <button data-copiar="#id-do-textarea">
  document.addEventListener("click", async (evento) => {
    const botao = evento.target.closest("[data-copiar]");
    if (!botao) return;
    const alvo = document.querySelector(botao.dataset.copiar);
    if (!alvo) return;
    try {
      await navigator.clipboard.writeText(alvo.value ?? alvo.textContent);
      mostrarToast({ mensagem: "Copiado. Cole na conversa com o Claude.", tipo: "ok" });
    } catch {
      alvo.select?.();
      mostrarToast({ mensagem: "Não deu para copiar sozinho: o texto ficou selecionado, use Ctrl+C.", tipo: "info" });
    }
  });

  // Toast declarativo: <button data-toast="Mensagem" data-toast-tipo="erro">
  document.addEventListener("click", (evento) => {
    const el = evento.target.closest("[data-toast]");
    if (el) mostrarToast({ mensagem: el.dataset.toast, tipo: el.dataset.toastTipo || "info" });
  });

  document.addEventListener("htmx:responseError", (evento) => {
    if (evento.detail.target === drawerConteudo) drawer.close();
    mostrarToast({ mensagem: `Não foi possível concluir (erro ${evento.detail.xhr.status}).`, tipo: "erro" });
  });
  document.addEventListener("htmx:sendError", () => {
    if (drawer.open) drawer.close();
    mostrarToast({ mensagem: "O app não respondeu. Ele ainda está aberto?", tipo: "erro" });
  });

  /* ───────── Campo de moeda ───────── */
  // Só formata o texto ("1.234,56", estilo caixa eletrônico). Sem float: tudo é texto.
  // O servidor lê o valor com centavos_de_texto().
  function formatarMoeda(texto, aceitaNegativo) {
    const negativo = aceitaNegativo && /^\s*[-−]/.test(texto);
    const limpo = texto.replace(/\D/g, "").replace(/^0+/, "").slice(0, 13);
    if (!limpo) return negativo ? "-" : "";
    const completo = limpo.padStart(3, "0");
    const inteiro = completo.slice(0, -2).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    return `${negativo ? "-" : ""}${inteiro},${completo.slice(-2)}`;
  }

  document.addEventListener("input", (evento) => {
    const campo = evento.target;
    if (campo.matches("[data-moeda]")) {
      campo.value = formatarMoeda(campo.value, campo.dataset.moeda === "negativo");
    }
  });

  /* ───────── Busca / paleta (Ctrl+K) ───────── */
  const paleta = $("#paleta");
  const paletaEntrada = $("#paleta-entrada");
  const opcoes = $$("#paleta-lista [role=option]");
  const normalizar = (texto) =>
    texto.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  let visiveis = [];
  let indice = 0;

  function selecionar(novo) {
    opcoes.forEach((opcao) => opcao.setAttribute("aria-selected", "false"));
    if (!visiveis.length) {
      paletaEntrada.removeAttribute("aria-activedescendant");
      return;
    }
    indice = (novo + visiveis.length) % visiveis.length;
    const opcao = visiveis[indice];
    opcao.setAttribute("aria-selected", "true");
    paletaEntrada.setAttribute("aria-activedescendant", opcao.id);
    opcao.scrollIntoView({ block: "nearest" });
  }

  function filtrar() {
    const termo = normalizar(paletaEntrada.value.trim());
    visiveis = opcoes.filter((opcao) => {
      const combina = normalizar(opcao.dataset.busca).includes(termo);
      opcao.parentElement.hidden = !combina;
      return combina;
    });
    $("#paleta-vazia").hidden = visiveis.length > 0;
    selecionar(0);
  }

  function abrirPaleta() {
    if (paleta.open) return;
    paletaEntrada.value = "";
    filtrar();
    paleta.showModal();
    paletaEntrada.focus();
  }

  paletaEntrada.addEventListener("input", filtrar);
  paletaEntrada.addEventListener("keydown", (evento) => {
    if (evento.key === "ArrowDown" || evento.key === "ArrowUp") {
      evento.preventDefault();
      selecionar(indice + (evento.key === "ArrowDown" ? 1 : -1));
    } else if (evento.key === "Enter" && visiveis[indice]) {
      evento.preventDefault();
      visiveis[indice].click();
    }
  });
  $("#abrir-paleta").addEventListener("click", abrirPaleta);

  /* ───────── Atalhos de teclado ───────── */
  // N = novo lançamento · ← → = mês · Ctrl+K = busca. Ignorados ao digitar ou com diálogo aberto.
  const ATALHOS = { n: "novo", N: "novo", ArrowLeft: "mes-anterior", ArrowRight: "mes-proximo" };
  const editando = (el) =>
    el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));

  document.addEventListener("keydown", (evento) => {
    if ((evento.ctrlKey || evento.metaKey) && evento.key.toLowerCase() === "k") {
      evento.preventDefault();
      abrirPaleta();
      return;
    }
    if (evento.key === "Escape" && html.classList.contains("menu-aberto")) {
      fecharMenu();
      return;
    }
    if (evento.ctrlKey || evento.metaKey || evento.altKey || evento.repeat) return;
    if (editando(evento.target) || $("dialog[open]")) return;
    const alvo = $(`[data-atalho="${ATALHOS[evento.key]}"]`);
    if (ATALHOS[evento.key] && alvo) {
      evento.preventDefault();
      alvo.click();
    }
  });

  // Botões secundários que disparam a mesma ação de um atalho: data-atalho-clique="novo".
  document.addEventListener("click", (evento) => {
    const el = evento.target.closest("[data-atalho-clique]");
    if (el) $(`[data-atalho="${el.dataset.atalhoClique}"]`)?.click();
  });

  window.MyEdinhos = { confirmar, mostrarToast };
})();

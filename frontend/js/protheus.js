/* Construtor Visual de consultas (estilo APSDU). */
import { api, auth, toast, withSpinner, formatBR, loadPublicSettings } from "./api.js";
import { renderLayout } from "./layout.js";
import { runReportJob } from "./jobs.js";

renderLayout({ active: "protheus", title: "Construtor de Consultas Protheus" });

const canExport = auth.hasAction("export");
// Nomes FISICOS das colunas (D1_DOC, etc.) so aparecem para admin; operador ve
// apenas o nome humanizado (SX3).
const isAdmin = auth.isAdmin();

const OPERATORS = [
  { v: "eq",      label: "Igual a (=)" },
  { v: "ne",      label: "Diferente de (≠)" },
  { v: "gt",      label: "Maior que (>)" },
  { v: "gte",     label: "Maior ou igual (≥)" },
  { v: "lt",      label: "Menor que (<)" },
  { v: "lte",     label: "Menor ou igual (≤)" },
  { v: "like",    label: "Contém" },
];

document.getElementById("page").innerHTML = `
  <!-- Sprint 8 — Barra "Meus Modelos" (consultas salvas em localStorage) -->
  <div class="builder-panel mb-3 saved-models-bar">
    <div class="d-flex flex-wrap align-items-center gap-2">
      <span class="fw-semibold">⭐ Meus Modelos</span>
      <select id="savedModelsSel" class="form-select form-select-sm" style="max-width:340px">
        <option value="">— Carregar modelo salvo —</option>
      </select>
      <button class="btn btn-sm btn-outline-primary" id="btnLoadModel" disabled>📂 Carregar</button>
      <button class="btn btn-sm btn-success" id="btnSaveModel">
        <span class="spinner-border spinner-border-sm me-2 d-none" role="status"></span>
        <span class="label">💾 Salvar Modelo</span>
      </button>
      <button class="btn btn-sm btn-outline-danger" id="btnDeleteModel" disabled>🗑️ Excluir</button>
      <span class="text-muted small ms-auto" id="savedModelsCount">0 modelos salvos</span>
    </div>
    <div class="form-text">
      Salva tabela + filial + JOINs + colunas + filtros no <strong>seu</strong> navegador
      (localStorage). Útil para consultas recorrentes sem precisar criar agendamento.
    </div>
  </div>

  <div class="builder-panel mb-3">
    <h5>1 · Selecione o módulo, a tabela e a filial</h5>
    <div class="row g-2 align-items-end">
      <div class="col-md-3">
        <label class="form-label small mb-1">Módulo</label>
        <select id="moduleSel" class="form-select">
          <option value="">Todos os meus módulos</option>
        </select>
      </div>
      <div class="col-md-4">
        <label class="form-label small mb-1">Tabela base (Alias Protheus)</label>
        <select id="aliasSel" class="form-select">
          <option value="">Carregando…</option>
        </select>
      </div>
      <div class="col-md-2">
        <label class="form-label small mb-1">Filial</label>
        <select id="branchSel" class="form-select" disabled>
          <option value="">—</option>
        </select>
      </div>
      <div class="col-md-3">
        <label class="form-label small mb-1">Tabela física resolvida</label>
        <input id="resolvedTable" class="form-control" readonly placeholder="(escolha)">
      </div>
    </div>

    <!-- Sprint 5: bloco de relacionamentos (JOIN) -->
    <div class="join-block mt-3">
      <div class="d-flex justify-content-between align-items-center mb-2">
        <span class="small text-muted">
          Relacionamentos (JOIN) — opcional. Permite cruzar a tabela base com outras.
        </span>
        <button class="btn btn-sm btn-outline-primary" id="btnAddJoin" disabled>
          + Adicionar Relacionamento
        </button>
      </div>
      <div id="joinsBox"></div>
    </div>
  </div>

  <div class="builder mb-3">
    <div class="builder-panel">
      <div class="d-flex justify-content-between align-items-center mb-2">
        <h5 class="m-0">2 · Colunas a exibir</h5>
        <div class="d-flex gap-2">
          <button class="btn btn-sm btn-outline-secondary" id="btnAllCols">Todas</button>
          <button class="btn btn-sm btn-outline-secondary" id="btnNoneCols">Nenhuma</button>
        </div>
      </div>
      <!-- Sprint 5: filtro de busca em tempo real (crítico p/ UX com JOINs) -->
      <div class="column-search-wrap">
        <input id="column-search" type="text" class="form-control form-control-sm column-search"
               placeholder="🔎 Pesquisar coluna (físico C5_NUM ou descrição)…"
               autocomplete="off">
        <span class="column-search-count text-muted small" id="column-search-count"></span>
      </div>
      <div id="colsBox" class="col-grid mt-2">
        <div class="text-muted small p-2">Selecione tabela e filial para listar as colunas.</div>
      </div>
      <div class="form-text mt-2" id="colsFootHint">
        Colunas aparecem prefixadas pelo alias da tabela. Em modo JOIN, escolha o que cruzar.
      </div>
    </div>

    <div class="builder-panel">
      <div class="d-flex justify-content-between align-items-center mb-2">
        <h5 class="m-0">3 · Filtros</h5>
        <button class="btn btn-sm btn-outline-primary" id="btnAddFilter">+ Adicionar filtro</button>
      </div>
      <div id="filtersBox"></div>
      <div class="form-text mt-2">Múltiplos filtros são combinados com <strong>E</strong> (AND).</div>
    </div>
  </div>

  <div class="d-flex flex-wrap align-items-end gap-2 mb-3">
    <div>
      <label class="form-label small mb-1">Página</label>
      <input id="page" type="number" class="form-control" value="1" style="width:90px">
    </div>
    <div>
      <label class="form-label small mb-1">Tamanho</label>
      <input id="psize" type="number" class="form-control" value="100" style="width:110px">
    </div>
    <button id="btnQuery" class="btn btn-primary">
      <span class="spinner-border spinner-border-sm me-2 d-none" role="status"></span>
      <span class="label">Consultar</span>
    </button>
    <!-- Sprint 11: Reseta TODO o Builder (modulo, alias, branch, joins, cols, filtros, grid) -->
    <button id="btnClearForm" class="btn btn-outline-secondary"
            title="Limpa modulo, tabela, JOINs, colunas, filtros e resultado">
      🧹 Limpar Formulário
    </button>
    ${canExport ? `
      <div class="btn-group" title="Download imediato — limite 5.000 linhas">
        <button class="btn btn-outline-secondary dropdown-toggle" data-bs-toggle="dropdown">
          <span class="spinner-border spinner-border-sm me-2 d-none" role="status" id="expSpin"></span>
          <span class="label">📥 Baixar Excel (Rápido)</span>
        </button>
        <ul class="dropdown-menu">
          <li><a class="dropdown-item exp" data-fmt="xlsx" href="#">Excel (.xlsx)</a></li>
          <li><a class="dropdown-item exp" data-fmt="csv"  href="#">CSV</a></li>
          <li><a class="dropdown-item exp" data-fmt="pdf"  href="#">PDF</a></li>
          <li><a class="dropdown-item exp" data-fmt="ods"  href="#">ODS</a></li>
        </ul>
      </div>
      <div class="btn-group" title="Gera em background e disponibiliza para download — recomendado para arquivos grandes">
        <button class="btn btn-success dropdown-toggle" data-bs-toggle="dropdown">
          <span class="label">📨 Gerar em Background (Arquivos Grandes)</span>
        </button>
        <ul class="dropdown-menu">
          <li><a class="dropdown-item bigexp" data-fmt="xlsx" href="#">XLSX (streaming)</a></li>
          <li><a class="dropdown-item bigexp" data-fmt="csv"  href="#">CSV (streaming)</a></li>
        </ul>
      </div>` : ""}
  </div>

  <div class="table-card">
    <div class="table-toolbar d-flex align-items-center justify-content-between">
      <span class="text-muted small" id="meta">—</span>
      <!-- Sprint 5 UX polish: paginacao visivel -->
      <span class="pagination-indicator" id="pagIndicator" style="display:none">
        Exibindo página <strong id="pagCurrent">—</strong>
        <span class="text-muted">de <span id="pagTotal">—</span></span>
      </span>
    </div>
    <div class="table-responsive">
      <table class="table table-sm table-hover mb-0" id="grid">
        <thead></thead><tbody></tbody>
      </table>
    </div>
  </div>
`;

// ---- estado em memória ------------------------------------------------------
const state = {
  alias: "",                   // alias logico Protheus da BASE (ex "SC5")
  branch: "",
  resolvedTable: "",
  // Sprint 5: colunas agora sao por TABELA
  //   columnsByAlias["SC5"] = [{name, type, ...}]
  //   columnsByAlias["SA1"] = [...]
  columnsByAlias: {},
  selectedCols: new Set(),     // strings "SC5.C5_NUM", "SA1.A1_NOME"
  filters: [],                 // [{field qualificado, op, value}]
  tableSuffix: "0",
  module: "",
  allAliases: [],
  userProfiles: [],
  aliasLabel: {},              // SC5 → "Pedidos de Venda"
  // Sprint 5 — JOINs
  joins: [],                   // [{alias, branch, join_type, on:[{left_alias,left_column,right_column}]}]
};

// ---- helpers ----------------------------------------------------------------
function $(id) { return document.getElementById(id); }

async function loadAliases() {
  try {
    const data = await api("/api/protheus/aliases");
    state.tableSuffix = data.table_suffix || "0";
    state.allAliases  = data.aliases || [];
    state.userProfiles = data.user_profiles || [];
    state.aliasLabel = Object.fromEntries(
      (data.aliases || []).map(a => [a.alias, a.label])
    );

    // Preenche o select de modulos com os perfis do usuario
    const modSel = $("moduleSel");
    modSel.innerHTML =
      `<option value="">Todos os meus módulos</option>` +
      state.userProfiles
        .map(p => `<option value="${p}">${p}</option>`).join("");

    renderAliasOptions();
  } catch (e) { toast("Falha ao listar aliases: " + e.message, "danger"); }
}

function renderAliasOptions() {
  /* Re-renderiza o select de aliases segundo o filtro de modulo atual.

     Regra Fase 4.A: tags [PERFIL,PERFIL] sao exclusivas para ADMIN.
     Operador ve apenas "ALIAS — Nome amigavel" para nao expor a estrutura
     de perfis do sistema (Least-Privilege na UI tambem).
  */
  const filter = state.module;
  const aliases = filter
    ? state.allAliases.filter(a => (a.profiles || []).includes(filter))
    : state.allAliases;
  const sel = $("aliasSel");
  if (!aliases.length) {
    sel.innerHTML = `<option value="">(nenhuma tabela neste módulo)</option>`;
    return;
  }
  const isAdmin = auth.isAdmin();
  sel.innerHTML =
    `<option value="">— escolha a tabela —</option>` +
    aliases.map(a => {
      const tags = isAdmin && (a.profiles || []).length
        ? ` · [${a.profiles.join(",")}]`
        : "";
      return `<option value="${a.alias}">${a.alias} — ${a.label}${tags}</option>`;
    }).join("");
}

async function loadBranches(_aliasIgnored) {
  /* Filiais FIXAS da operacao Fertimaxi.

     Comportamento (Fase 4):
     - Lista vem de /api/settings/public (configuravel via Admin).
     - Independe de a tabela ter ou nao registros (antes, o dropdown
       quebrava em tabelas vazias — agora sempre exibe a lista fixa).
     - Sem opcao de digitacao manual; usuario deve selecionar.

     Sprint 5: cacheia em `window._publicSettingsBranches` para o
     `renderJoins` reutilizar sem nova chamada.
  */
  const sel = $("branchSel");
  sel.disabled = true;
  sel.innerHTML = `<option value="">Carregando…</option>`;
  const fallback = ["01","02","03","04","05","06","07","08"];
  try {
    const cfg = await loadPublicSettings();
    const branches = Array.isArray(cfg.branches) && cfg.branches.length
      ? cfg.branches : fallback;
    window._publicSettingsBranches = branches;
    sel.innerHTML =
      `<option value="">— escolha a filial —</option>` +
      branches.map(b => `<option value="${b}">${b}</option>`).join("");
    sel.disabled = false;
  } catch {
    window._publicSettingsBranches = fallback;
    sel.innerHTML =
      `<option value="">— escolha a filial —</option>` +
      fallback.map(b => `<option value="${b}">${b}</option>`).join("");
    sel.disabled = false;
  }
}

function recomputeResolvedTable() {
  if (state.alias && state.branch) {
    state.resolvedTable = `${state.alias}${state.branch}${state.tableSuffix}`;
  } else {
    state.resolvedTable = "";
  }
  $("resolvedTable").value = state.resolvedTable;
}

async function loadColumns() {
  /* Sprint 5: carrega colunas da BASE + de TODAS as tabelas dos JOINs.
     Cada chamada e' a /api/protheus/columns?alias=X&branch=Y (1 round-trip por tabela).
     Mantemos a estrutura `state.columnsByAlias[<ALIAS>] = [...]` para a UI
     mesclar a listagem.
  */
  if (!state.alias || !state.branch) return;
  recomputeResolvedTable();
  const box = $("colsBox");
  box.innerHTML = `<div class="text-muted small p-2">Carregando colunas…</div>`;

  // Lista de (alias, branch) a buscar: base + cada JOIN
  const targets = [
    { alias: state.alias, branch: state.branch },
    ...state.joins.map(j => ({ alias: j.alias, branch: j.branch })),
  ].filter(t => t.alias && t.branch);

  state.columnsByAlias = {};
  const errors = [];

  for (const t of targets) {
    try {
      const data = await api(`/api/protheus/columns?alias=${t.alias}&branch=${t.branch}`);
      state.columnsByAlias[t.alias] = data.columns;
    } catch (e) {
      errors.push({ alias: t.alias, message: String(e.message || "") });
      state.columnsByAlias[t.alias] = [];
    }
  }

  if (errors.length && !Object.values(state.columnsByAlias).some(c => c.length)) {
    // Sem nenhuma coluna carregada — mostra mensagem do PRIMEIRO erro
    const e = errors[0];
    const msg = e.message;
    let html;
    if (msg.includes("nao existe") || msg.includes("não existe")) {
      html = `
        <div class="alert alert-warning small mb-0">
          <strong>Esta filial ainda não tem registros para ${e.alias}.</strong><br>
          O Protheus cria a tabela física apenas após o primeiro lançamento.
        </div>`;
    } else if (msg.toLowerCase().includes("permiss") || msg.toLowerCase().includes("autoriz")) {
      html = `
        <div class="alert alert-danger small mb-0">
          <strong>Sem permissão para a tabela ${e.alias}.</strong>
        </div>`;
    } else {
      html = `<div class="alert alert-danger small mb-0">${msg}</div>`;
    }
    box.innerHTML = html;
    state.selectedCols.clear();
    renderFilters();
    return;
  }

  // v2.29 — por padrao, TODAS as colunas ja vem marcadas (base + JOINs).
  // O fluxo de "Carregar modelo" sobrescreve com a selecao salva logo apos.
  state.selectedCols = new Set();
  for (const alias of Object.keys(state.columnsByAlias)) {
    for (const c of (state.columnsByAlias[alias] || [])) {
      state.selectedCols.add(`${alias}.${c.name}`);
    }
  }
  renderColumns();
  renderFilters();
}

function renderColumns() {
  /* Sprint 5: mescla colunas de TODAS as tabelas (base + JOINs).
     Cada checkbox carrega `value="ALIAS.COLUNA"` (qualificada).
     Grupos por tabela com cabecalho colorido para distinguir.
  */
  const box = $("colsBox");
  const aliases = Object.keys(state.columnsByAlias);
  if (!aliases.length) {
    box.innerHTML = `<div class="text-muted small p-2">Nenhuma coluna encontrada.</div>`;
    return;
  }
  // Conta total de colunas para mostrar no contador de busca
  let totalCols = 0;
  const groups = aliases.map(alias => {
    const cols = state.columnsByAlias[alias] || [];
    totalCols += cols.length;
    const label = state.aliasLabel[alias] || alias;
    if (!cols.length) {
      return `<div class="col-group">
        <div class="col-group-header">${alias} — ${label} <em>(sem colunas carregadas)</em></div>
      </div>`;
    }
    return `<div class="col-group" data-alias="${alias}">
      <div class="col-group-header">
        <span class="badge bg-secondary me-1">${alias}</span>
        ${label}
        <span class="text-muted small">(${cols.length})</span>
      </div>
      <div class="col-group-body">
        ${cols.map(c => {
          const qualified = `${alias}.${c.name}`;
          const checked = state.selectedCols.has(qualified);
          const desc = c.description ? c.description.replace(/"/g,"&quot;") : "";
          const typeTitle = `${c.type || ""}${c.max_length ? ' ('+c.max_length+')' : ''}`;
          // data-search agrega: alias.col_fisico + descricao (para o filtro de busca)
          const searchKey = (`${qualified} ${c.name} ${desc}`).toLowerCase();
          return `
            <div class="form-check col-item" data-search="${searchKey}">
              <input class="form-check-input col-check" type="checkbox"
                     id="col_${alias}_${c.name}" value="${qualified}"
                     ${checked ? "checked" : ""}>
              <label class="form-check-label small" for="col_${alias}_${c.name}"
                     title="${isAdmin ? c.name : (desc || c.name)}${typeTitle ? ' · '+typeTitle : ''}">
                ${desc
                  ? (isAdmin
                      ? `<strong>${desc}</strong> <span class="text-muted">(${c.name})</span>`
                      : `<strong>${desc}</strong>`)
                  : `<strong>${c.name}</strong>`}
              </label>
            </div>`;
        }).join("")}
      </div>
    </div>`;
  }).join("");
  box.innerHTML = groups;

  box.querySelectorAll(".col-check").forEach(c =>
    c.addEventListener("change", e => {
      const v = e.target.value;
      e.target.checked ? state.selectedCols.add(v) : state.selectedCols.delete(v);
    })
  );

  $("column-search-count").textContent = `${totalCols} coluna(s)`;
  applyColumnSearch();   // re-aplica filtro se o input ja tem texto
}


// ---- Sprint 5: filtro de busca em tempo real ------------------------------
function applyColumnSearch() {
  const q = ($("column-search")?.value || "").trim().toLowerCase();
  const box = $("colsBox");
  if (!box) return;
  let visible = 0;
  const items = box.querySelectorAll(".col-item[data-search]");
  items.forEach(el => {
    if (!q || el.dataset.search.includes(q)) {
      el.style.display = "";
      visible++;
    } else {
      el.style.display = "none";
    }
  });
  // Esconde grupos sem coluna visivel
  box.querySelectorAll(".col-group[data-alias]").forEach(g => {
    const anyVisible = g.querySelector(".col-item:not([style*='display: none'])");
    g.style.display = anyVisible ? "" : "none";
  });

  const total = items.length;
  $("column-search-count").textContent = q
    ? `${visible}/${total} colunas`
    : `${total} coluna(s)`;
}

function _allQualifiedColumns() {
  /* Util: gera lista plana de "ALIAS.COL — descricao" de TODAS as tabelas
     ativas (base + joins). Usado pelos dropdowns de filtros e do ON dos JOINs.
  */
  const out = [];
  for (const alias of Object.keys(state.columnsByAlias)) {
    for (const c of state.columnsByAlias[alias] || []) {
      out.push({
        value: `${alias}.${c.name}`,
        // Humanizado: operador ve so o titulo; admin ve "Titulo (ALIAS.FISICO)".
        label: c.description
          ? (isAdmin ? `${c.description} (${alias}.${c.name})` : c.description)
          : `${alias}.${c.name}`,
        alias, name: c.name,
      });
    }
  }
  return out;
}

function renderFilters() {
  const box = $("filtersBox");
  if (!state.filters.length) {
    box.innerHTML = `<div class="text-muted small">Nenhum filtro — todos os registros (até o limite da página) serão retornados.</div>`;
    return;
  }
  const colOpts = _allQualifiedColumns()
    .map(c => `<option value="${c.value}">${c.label}</option>`).join("");
  const opOpts = OPERATORS
    .map(o => `<option value="${o.v}">${o.label}</option>`).join("");

  box.innerHTML = state.filters.map((f, i) => `
    <div class="filter-row" data-idx="${i}">
      <select class="form-select form-select-sm f-field">
        <option value="">— campo —</option>${colOpts}
      </select>
      <select class="form-select form-select-sm f-op">${opOpts}</select>
      <input class="form-control form-control-sm f-value"
             placeholder="Valor (datas: dd/mm/aaaa)"
             title="Para campos de data, digite no formato dd/mm/aaaa (ex: 31/12/2026). O sistema converte automaticamente para o formato Protheus.">
      <button type="button" class="btn-remove" title="Remover">✕</button>
    </div>
  `).join("");

  state.filters.forEach((f, i) => {
    const row = box.querySelector(`[data-idx="${i}"]`);
    const fld = row.querySelector(".f-field");
    const op  = row.querySelector(".f-op");
    const val = row.querySelector(".f-value");
    fld.value = f.field || "";
    op.value  = f.op || "eq";
    val.value = f.value ?? "";
    // Sprint 6 — Date-picker nativo quando o campo e' de data
    _applyDatePicker(val, f.field);

    // Sprint 14 — Select2 (substituiu Choices.js) no select de coluna do
    // filtro. Renderiza UI custom com input de busca interno. Quando o usuario
    // troca o campo, dispara evento `change` que delegamos para o handler
    // nativo abaixo via .on('change').
    const $jq = window.jQuery;
    if ($jq && $jq.fn && $jq.fn.select2) {
      try {
        $jq(fld).select2({
          theme: "bootstrap-5",
          placeholder: "— campo —",
          allowClear: false,
          width: "100%",
          dropdownParent: $jq(fld).closest(".filter-row"),
        });
        if (f.field) {
          $jq(fld).val(f.field).trigger("change.select2");
        }
        // Select2 dispara change no jQuery; replicamos a logica antiga
        $jq(fld).on("change", function () {
          f.field = this.value;
          _applyDatePicker(val, f.field);
          val.value = "";
          f.value = "";
        });
      } catch (e) {
        // Select2 indisponivel — cai pro change nativo abaixo
      }
    }

    // Fallback: listener nativo (caso Select2 nao tenha sido carregado)
    fld.addEventListener("change", e => {
      f.field = e.target.value;
      _applyDatePicker(val, f.field);
      val.value = "";
      f.value = "";
    });
    op.addEventListener("change",    e => f.op    = e.target.value);
    val.addEventListener("input",    e => f.value = e.target.value);
    row.querySelector(".btn-remove").addEventListener("click", () => {
      state.filters.splice(i, 1);
      renderFilters();
    });
  });
}


// Sprint 6 — detecta colunas de data e troca o input para type="date".
// Heuristica por sufixo do nome do campo Protheus (compativel com aliasificado
// SC5__C5_EMISSAO ou simples C5_EMISSAO).
function _isDateField(field) {
  if (!field) return false;
  // Suporta "SC5__C5_EMISSAO" → considera so a parte depois de "__"
  const f = field.includes("__") ? field.split("__").pop() : field;
  return /(_EMISSAO|_DTDIGIT|_DATA[A-Z0-9_]*|_VENC[A-Z0-9_]*|_DT[A-Z0-9_]*)$/i.test(f);
}

function _applyDatePicker(inputEl, field) {
  if (!inputEl) return;
  if (_isDateField(field)) {
    inputEl.type = "date";
    inputEl.placeholder = "";
    inputEl.title = "Selecione a data (formato dd/mm/aaaa será convertido para Protheus)";
  } else {
    inputEl.type = "text";
    inputEl.placeholder = "Valor (datas: dd/mm/aaaa)";
    inputEl.title = "Para campos de data, digite dd/mm/aaaa — o sistema converte para o formato Protheus.";
  }
}


// ============================================================
//  Sprint 5 — Bloco de JOINs (relacionamentos)
// ============================================================

function renderJoins() {
  const box = $("joinsBox");
  // Atualiza contador + estado do botao "+"
  const addBtn = $("btnAddJoin");
  if (addBtn) {
    const label = addBtn.querySelector(".label") || addBtn;
    const baseLabel = "+ Adicionar Relacionamento";
    label.textContent = state.joins.length
      ? `${baseLabel} (${state.joins.length}/${MAX_JOINS})`
      : baseLabel;
    if (state.alias && state.branch) {
      addBtn.disabled = state.joins.length >= MAX_JOINS;
    }
  }
  // Sprint 5 UX polish: indicador dos perfis do user corrente. O dropdown "Tabela B"
  // ja vem filtrado pelo backend (/api/protheus/aliases respeita perfis/whitelist),
  // mas mostrar visualmente cria confianca e previne suporte ("por que nao vejo SX5?").
  const profilesInfo = !auth.isAdmin() && state.userProfiles.length
    ? `<div class="join-profiles-hint small text-muted mb-2">
         🔒 Apenas tabelas dos seus perfis estão disponíveis:
         ${state.userProfiles.map(p => `<span class="badge bg-info text-dark me-1">${p}</span>`).join("")}
       </div>` : "";
  if (!state.joins.length) {
    box.innerHTML = `${profilesInfo}
      <div class="text-muted small">Sem relacionamentos. Adicione um para cruzar a tabela base com outra (ex: SC5 com SA1 via C5_CLIENTE = A1_COD).</div>`;
    return;
  }
  // Aliases disponiveis para "Tabela B" — somente os do allAliases (ja filtrado pelo backend)
  // EXCETO os ja usados na consulta atual.
  const usedAliases = new Set([state.alias, ...state.joins.map(j => j.alias)]);
  const branches = Array.isArray(window._publicSettingsBranches) && window._publicSettingsBranches.length
    ? window._publicSettingsBranches
    : ["01","02","03","04","05","06","07","08"];

  box.innerHTML = profilesInfo + state.joins.map((j, idx) => {
    const aliasOpts = state.allAliases
      .filter(a => !usedAliases.has(a.alias) || a.alias === j.alias)
      .map(a => `<option value="${a.alias}" ${a.alias === j.alias ? "selected" : ""}>${a.alias} — ${a.label}</option>`)
      .join("");
    const branchOpts = branches
      .map(b => `<option value="${b}" ${b === j.branch ? "selected" : ""}>${b}</option>`).join("");
    // "Tabela A" da condicao ON = base + joins anteriores (no jdx atual)
    const previousAliases = [state.alias, ...state.joins.slice(0, idx).map(x => x.alias)];

    return `
    <div class="join-card" data-jdx="${idx}">
      <div class="join-card-header">
        <span class="join-badge">${idx + 1}</span>
        <select class="form-select form-select-sm j-type" style="max-width:280px"
                title="INNER mantém apenas registros da base que casam com a tabela B. LEFT mantém TODOS da base e completa quando houver correspondência.">
          <option value="INNER" ${j.join_type === "INNER" ? "selected" : ""}>Obrigatório ter correspondência (INNER)</option>
          <option value="LEFT"  ${j.join_type === "LEFT"  ? "selected" : ""}>Opcional ter correspondência (LEFT)</option>
        </select>
        <span class="small text-muted">com</span>
        <select class="form-select form-select-sm j-alias">
          <option value="">— escolha tabela B —</option>${aliasOpts}
        </select>
        <select class="form-select form-select-sm j-branch" style="max-width:90px">${branchOpts}</select>
        <button type="button" class="btn-remove ms-auto j-remove" title="Remover JOIN">✕</button>
      </div>
      <div class="join-card-body">
        <div class="small text-muted mb-1">Condição ON (relaciona tabela B com alguma já existente):</div>
        ${j.on.map((c, ci) => `
          <div class="on-row" data-ci="${ci}">
            <select class="form-select form-select-sm on-left-alias">
              ${previousAliases.map(a => `<option value="${a}" ${a === c.left_alias ? "selected" : ""}>${a}</option>`).join("")}
            </select>
            <span class="small">.</span>
            <select class="form-select form-select-sm on-left-col">
              <option value="">— coluna A —</option>
              ${_columnsOfAlias(c.left_alias || previousAliases[0])
                  .map(co => `<option value="${co.name}" ${co.name === c.left_column ? "selected" : ""}>${co.name}</option>`).join("")}
            </select>
            <span class="small">=</span>
            <span class="small text-muted">${j.alias || "(escolha B)"}</span>
            <span class="small">.</span>
            <select class="form-select form-select-sm on-right-col">
              <option value="">— coluna B —</option>
              ${_columnsOfAlias(j.alias).map(co => `<option value="${co.name}" ${co.name === c.right_column ? "selected" : ""}>${co.name}</option>`).join("")}
            </select>
            <button type="button" class="btn-remove on-remove" title="Remover condição">✕</button>
          </div>
        `).join("")}
        <button type="button" class="btn btn-link btn-sm ps-0 j-add-on">+ AND mais uma condição</button>
      </div>
    </div>`;
  }).join("");

  // Listeners
  box.querySelectorAll(".join-card").forEach(card => {
    const jdx = parseInt(card.dataset.jdx, 10);
    const j = state.joins[jdx];
    card.querySelector(".j-type").addEventListener("change", e => { j.join_type = e.target.value; });
    card.querySelector(".j-alias").addEventListener("change", async e => {
      j.alias = e.target.value;
      // Ao trocar a tabela B, recarrega colunas + redesenha
      await loadColumns();
      renderJoins();
    });
    card.querySelector(".j-branch").addEventListener("change", async e => {
      j.branch = e.target.value;
      await loadColumns();
      renderJoins();
    });
    card.querySelector(".j-remove").addEventListener("click", async () => {
      state.joins.splice(jdx, 1);
      await loadColumns();
      renderJoins();
    });
    card.querySelector(".j-add-on").addEventListener("click", () => {
      j.on.push({ left_alias: state.alias, left_column: "", right_column: "" });
      renderJoins();
    });
    card.querySelectorAll(".on-row").forEach(row => {
      const ci = parseInt(row.dataset.ci, 10);
      const cond = j.on[ci];
      row.querySelector(".on-left-alias").addEventListener("change", e => {
        cond.left_alias = e.target.value;
        renderJoins();
      });
      row.querySelector(".on-left-col").addEventListener("change", e => cond.left_column = e.target.value);
      row.querySelector(".on-right-col").addEventListener("change", e => cond.right_column = e.target.value);
      row.querySelector(".on-remove").addEventListener("click", () => {
        if (j.on.length > 1) {
          j.on.splice(ci, 1);
          renderJoins();
        } else {
          toast("Cada JOIN precisa de pelo menos 1 condição ON", "warning");
        }
      });
    });
  });
}

function _columnsOfAlias(alias) {
  return state.columnsByAlias[alias] || [];
}

// ---- payload ----------------------------------------------------------------
function buildPayload() {
  if (!state.alias) throw new Error("Selecione a tabela (Alias) no passo 1.");
  if (!state.branch) {
    throw new Error("Selecione uma filial no passo 1. A filial é obrigatória para resolver a tabela física do Protheus.");
  }

  // Sprint 5: valida JOINs (se houver)
  const joinsOut = [];
  for (let i = 0; i < state.joins.length; i++) {
    const j = state.joins[i];
    if (!j.alias)  throw new Error(`Relacionamento #${i+1}: escolha a tabela B`);
    if (!j.branch) throw new Error(`Relacionamento #${i+1}: escolha a filial da tabela B`);
    if (!j.on.length) throw new Error(`Relacionamento #${i+1}: defina ao menos 1 condição ON`);
    for (let ci = 0; ci < j.on.length; ci++) {
      const c = j.on[ci];
      if (!c.left_alias || !c.left_column || !c.right_column) {
        throw new Error(`Relacionamento #${i+1}: complete a condição ON #${ci+1}`);
      }
    }
    joinsOut.push({
      alias: j.alias, branch: j.branch,
      join_type: j.join_type || "INNER",
      on: j.on.map(c => ({
        left_alias: c.left_alias,
        left_column: c.left_column,
        right_column: c.right_column,
      })),
    });
  }

  // Em modo JOIN, exigimos colunas selecionadas (evita SELECT * gigante)
  if (joinsOut.length && state.selectedCols.size === 0) {
    throw new Error("Com JOIN ativo, escolha ao menos uma coluna no passo 2.");
  }

  const rules = state.filters
    .filter(r => r.field)
    .map(r => ({ field: r.field, op: r.op || "eq", value: _normalizeDateBR(r.value) }));

  return {
    alias: state.alias,
    branch: state.branch,
    columns: [...state.selectedCols],
    rules,
    joins: joinsOut.length ? joinsOut : undefined,
    page: parseInt($("page").value || "1", 10),
    page_size: parseInt($("psize").value || "100", 10),
  };
}


// Sprint 5 UX polish + Sprint 6: detecta:
//   DD/MM/YYYY ou DD-MM-YYYY  → texto livre brasileiro
//   YYYY-MM-DD                → input type="date" nativo (HTML5)
// Converte para YYYYMMDD (formato Protheus). Mantem valores nao-data
// inalterados. Aplica em valores escalares E em listas (op `in`, `between`).
function _normalizeDateBR(value) {
  if (Array.isArray(value)) return value.map(_normalizeDateBR);
  if (typeof value !== "string") return value;
  const v = value.trim();
  // Formato ISO do <input type="date"> — YYYY-MM-DD
  const iso = v.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (iso) {
    const [_, yyyy, mm, dd] = iso;
    const d = parseInt(dd,10), mo = parseInt(mm,10);
    if (d < 1 || d > 31 || mo < 1 || mo > 12) return value;
    return `${yyyy}${mm}${dd}`;
  }
  // Formato brasileiro DD/MM/YYYY ou DD-MM-YYYY
  const m = v.match(/^(\d{2})[\/\-](\d{2})[\/\-](\d{4})$/);
  if (!m) return value;
  const [_, dd, mm, yyyy] = m;
  const d = parseInt(dd,10), mo = parseInt(mm,10);
  if (d < 1 || d > 31 || mo < 1 || mo > 12) return value;
  return `${yyyy}${mm}${dd}`;
}

let _gridDt = null;   // instancia DataTables do grid de Consultas (Sprint 22.3)

function _destroyGridDt() {
  const $jq = window.jQuery;
  const SEL = "#grid";
  if ($jq && $jq.fn && $jq.fn.DataTable && $jq.fn.DataTable.isDataTable(SEL)) {
    $jq(SEL).DataTable().clear().destroy();
  }
  _gridDt = null;
}

function renderGrid({ rows, total, page, page_size, table, columns_human }) {
  const meta = $("meta");
  const totalNum = Number(total) || 0;
  meta.textContent = `${table} · ${totalNum.toLocaleString("pt-BR")} registro(s)`;
  const human = columns_human || {};   // {coluna_fisica: titulo SX3}

  _destroyGridDt();   // pattern oficial DataTables — destroi antes de reconstruir

  // Indicador server-side antigo: o DataTables assume a paginacao (em cima/embaixo).
  const pagInd = $("pagIndicator");
  if (pagInd) pagInd.style.display = "none";

  const thead = document.querySelector("#grid thead");
  const tbody = document.querySelector("#grid tbody");
  thead.innerHTML = ""; tbody.innerHTML = "";
  if (!rows.length) {
    tbody.innerHTML = `<tr><td class="p-3 text-muted">Sem dados</td></tr>`;
    return;
  }
  const cols = Object.keys(rows[0]);
  // Cabecalho humanizado via SX3 (so o titulo); nome fisico vai no tooltip.
  thead.innerHTML = `<tr>${cols.map(c => {
    const title = human[c] && human[c] !== c ? human[c] : c;
    const tip = isAdmin ? c : title;   // nome fisico no tooltip so p/ admin
    return `<th title="${tip}">${title}</th>`;
  }).join("")}</tr>`;
  tbody.innerHTML = rows.map(r =>
    `<tr>${cols.map(c => `<td>${r[c] ?? ""}</td>`).join("")}</tr>`).join("");

  // Sprint 22.3 — paginacao DataTables igual a do Auditor, em CIMA e EMBAIXO.
  const $jq = window.jQuery;
  if ($jq && $jq.fn && $jq.fn.DataTable) {
    _gridDt = $jq("#grid").DataTable({
      paging: true,
      dom: '<"dt-top d-flex justify-content-between align-items-center flex-wrap gap-2"lp>'
         + 'rt'
         + '<"dt-bottom d-flex justify-content-between align-items-center flex-wrap gap-2"ip>',
      lengthMenu: [[10, 20, 50, 100, -1], [10, 20, 50, 100, "Todos"]],
      pageLength: 20,
      searching: false,
      info: true,
      order: [],
      language: {
        lengthMenu: "Mostrar _MENU_ registros por página",
        info: "Mostrando _START_ a _END_ de _TOTAL_ registros",
        infoEmpty: "0 registros",
        infoFiltered: "(filtrado de _MAX_ total)",
        paginate: { previous: "Anterior", next: "Próximo", first: "Primeiro", last: "Último" },
        zeroRecords: "Nenhum registro encontrado",
        emptyTable: "Nenhum registro encontrado",
      },
    });
  }
}

// ---- bindings ---------------------------------------------------------------
$("moduleSel").addEventListener("change", (e) => {
  state.module = e.target.value || "";
  state.alias = ""; state.branch = "";
  state.columnsByAlias = {}; state.selectedCols.clear(); state.filters = []; state.joins = [];
  renderColumns(); renderFilters(); renderJoins(); recomputeResolvedTable();
  renderAliasOptions();
  $("branchSel").innerHTML = `<option value="">—</option>`;
  $("branchSel").disabled = true;
  $("btnAddJoin").disabled = true;
});

// Sprint 5 — filtro de busca em colunas (tempo real)
$("column-search").addEventListener("input", applyColumnSearch);
$("column-search").addEventListener("keydown", e => {
  if (e.key === "Escape") { e.target.value = ""; applyColumnSearch(); }
});

// Sprint 5 — adicionar relacionamento (JOIN)
const MAX_JOINS = 5;   // sincronizado com backend (query_engine.DEFAULT_MAX_JOINS)
$("btnAddJoin").addEventListener("click", async () => {
  if (!state.alias || !state.branch) {
    return toast("Escolha a tabela base e a filial primeiro", "warning");
  }
  if (state.joins.length >= MAX_JOINS) {
    return toast(
      `Limite de ${MAX_JOINS} relacionamentos por consulta atingido — proteção do banco. ` +
      `Reduza o cruzamento ou peça ao admin para aumentar.`, "warning"
    );
  }
  state.joins.push({
    alias: "", branch: state.branch, join_type: "INNER",
    on: [{ left_alias: state.alias, left_column: "", right_column: "" }],
  });
  renderJoins();
  // Desabilita o botao quando bate o teto
  $("btnAddJoin").disabled = state.joins.length >= MAX_JOINS;
});

$("aliasSel").addEventListener("change", async (e) => {
  state.alias = e.target.value;
  state.branch = "";
  state.columnsByAlias = {};
  state.selectedCols.clear();
  state.filters = [];
  state.joins = [];                  // Sprint 5: troca de base limpa joins
  renderColumns(); renderFilters(); renderJoins();
  recomputeResolvedTable();
  $("btnAddJoin").disabled = true;
  if (state.alias) await loadBranches(state.alias);
});

$("branchSel").addEventListener("change", async (e) => {
  state.branch = e.target.value;
  recomputeResolvedTable();
  if (state.alias && state.branch) {
    await loadColumns();
    $("btnAddJoin").disabled = false;
  }
});

$("btnAllCols").addEventListener("click", () => {
  state.columns.forEach(c => state.selectedCols.add(c.name));
  renderColumns();
});
$("btnNoneCols").addEventListener("click", () => {
  state.selectedCols.clear();
  renderColumns();
});

$("btnAddFilter").addEventListener("click", () => {
  state.filters.push({ field: "", op: "eq", value: "" });
  renderFilters();
});

$("btnQuery").addEventListener("click", async () => {
  const btn = $("btnQuery");
  await withSpinner(btn, async () => {
    let payload;
    try { payload = buildPayload(); } catch (e) { return toast(e.message, "warning"); }
    // Sprint 8 — skeleton enquanto a consulta nao volta
    renderSkeleton(state.selectedCols.size || 6, Math.min(12, payload.page_size || 10));
    try {
      const data = await api("/api/protheus/query", { method: "POST", body: payload });
      renderGrid(data);
    } catch (e) {
      $("meta").textContent = "—";
      document.querySelector("#grid tbody").innerHTML = "";
      toast(e.message, "danger");
    }
  }, "Carregando…");
});

document.querySelectorAll(".exp").forEach(a => {
  a.addEventListener("click", async (e) => {
    e.preventDefault();
    let payload;
    try { payload = buildPayload(); } catch (err) { return toast(err.message, "warning"); }
    const fmt = a.dataset.fmt;
    const dropBtn = a.closest(".btn-group")?.querySelector(".dropdown-toggle");
    await withSpinner(dropBtn, async () => {
      try {
        const blob = await api(`/api/protheus/download?file_format=${fmt}`,
                               { method: "POST", body: payload });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `${state.resolvedTable || state.alias}.${fmt}`;
        link.click();
        URL.revokeObjectURL(url);
        toast(`Arquivo ${fmt.toUpperCase()} gerado`, "success");
      } catch (err) { toast(err.message, "danger"); }
    }, "Exportando…");
  });
});

// ---- Export grande (fila Celery) -------------------------------------------
document.querySelectorAll(".bigexp").forEach(a => {
  a.addEventListener("click", async (e) => {
    e.preventDefault();
    let payload;
    try { payload = buildPayload(); } catch (err) { return toast(err.message, "warning"); }
    const fmt = a.dataset.fmt;
    try {
      await runReportJob(payload, { format: fmt });
    } catch (err) {
      toast("Falha no job: " + err.message, "danger");
    }
  });
});

// ============================================================
//  Sprint 8 Part 1 — Consultas Salvas (server-side, per-user)
//
//  Sprint 8 (anterior) usava localStorage — limitado a 1 navegador. Agora
//  persiste em /api/saved-queries (tabela `saved_queries` no SQLite) e
//  acompanha o usuario em qualquer maquina.
// ============================================================

// Cache em memoria sincronizado com o servidor. Cada item: {id, name, payload}.
let _savedModels = [];
const LEGACY_LOCALSTORAGE_KEY = "pr_saved_models_v1";

function _findSavedModelById(id) {
  return _savedModels.find(m => String(m.id) === String(id));
}

async function _fetchSavedModels() {
  try {
    const r = await api("/api/saved-queries");
    _savedModels = r.items || [];
  } catch (e) {
    _savedModels = [];
    // Erro de rede / 401 — toasts so se nao for "401 Token invalido" inicial
    if (!/401|403/.test(String(e.message))) {
      toast("Falha ao listar modelos: " + e.message, "warning");
    }
  }
}

function _renderSavedModelsSelect() {
  const sel = $("savedModelsSel");
  if (!sel) return;
  sel.innerHTML = `<option value="">— Carregar modelo salvo —</option>` +
    _savedModels.map(m => `<option value="${m.id}">${m.name}</option>`).join("");
  const count = _savedModels.length;
  $("savedModelsCount").textContent =
    `${count} modelo${count === 1 ? "" : "s"} salvo${count === 1 ? "" : "s"}`;
  $("btnLoadModel").disabled = !sel.value;
  $("btnDeleteModel").disabled = !sel.value;
}

async function renderSavedModels() {
  await _fetchSavedModels();
  _renderSavedModelsSelect();
  // Sprint 8 Part 1 — migra automaticamente modelos antigos do localStorage
  // para o servidor na primeira execucao apos o upgrade.
  await _migrateLocalStorageOnce();
}

async function _migrateLocalStorageOnce() {
  /* Se houver `pr_saved_models_v1` no localStorage E o usuario nao tem
     modelos no servidor com esses nomes, sobe tudo via POST e marca como
     migrado (com sufixo no localStorage key). Idempotente. */
  let raw;
  try { raw = JSON.parse(localStorage.getItem(LEGACY_LOCALSTORAGE_KEY) || "[]"); }
  catch { raw = []; }
  if (!Array.isArray(raw) || !raw.length) return;

  const existingNames = new Set(_savedModels.map(m => m.name));
  let migrated = 0;
  for (const m of raw) {
    if (!m || !m.name || !m.alias) continue;
    if (existingNames.has(m.name)) continue;
    try {
      // Estrutura antiga (sem `id`) — sobe como payload novo
      const payload = {
        alias: m.alias, branch: m.branch, module: m.module,
        selected_columns: m.selected_columns || [],
        filters: m.filters || [],
        joins: m.joins || [],
      };
      await api("/api/saved-queries", { method: "POST",
        body: { name: m.name, payload },
      });
      migrated += 1;
    } catch (e) {
      // ignora individuais — outros podem subir
    }
  }
  if (migrated > 0) {
    toast(`${migrated} modelo(s) migrado(s) do navegador para o servidor`, "success");
    // Renomeia a chave antiga para nao re-migrar na proxima sessao
    localStorage.setItem(LEGACY_LOCALSTORAGE_KEY + "_migrated_at", new Date().toISOString());
    localStorage.removeItem(LEGACY_LOCALSTORAGE_KEY);
    // Refetch para incluir os recem migrados
    await _fetchSavedModels();
    _renderSavedModelsSelect();
  }
}

function _snapshotCurrentPayload() {
  /* Snapshot do estado do Builder. NAO inclui pagina/page_size — cada
     execucao escolhe os seus. Estrutura compativel com o payload aceito
     por /api/protheus/query (mas ignora `rules` que o backend espera —
     deixa para o buildPayload reaplicar). */
  return {
    alias: state.alias,
    branch: state.branch,
    module: state.module,
    selected_columns: [...state.selectedCols],
    filters: state.filters.map(f => ({ field: f.field, op: f.op, value: f.value })),
    joins: state.joins.map(j => ({
      alias: j.alias, branch: j.branch, join_type: j.join_type,
      on: (j.on || []).map(c => ({
        left_alias: c.left_alias, left_column: c.left_column,
        right_column: c.right_column,
      })),
    })),
  };
}

async function _applyModelToState(model) {
  /* Restaura modulo + alias + filial + JOINs + colunas + filtros.
     JOINs precisam estar em state.joins ANTES de loadColumns() para que
     o helper busque colunas de TODAS as tabelas envolvidas numa varredura. */
  const p = model.payload || {};
  if (!p.alias) return toast("Modelo invalido (sem alias).", "warning");

  state.module = p.module || "";
  $("moduleSel").value = state.module;
  renderAliasOptions();
  state.alias = p.alias;
  $("aliasSel").value = p.alias;
  await loadBranches(p.alias);
  state.branch = p.branch || "";
  $("branchSel").value = state.branch;
  state.joins = (p.joins || []).map(j => ({
    alias: j.alias, branch: j.branch || state.branch,
    join_type: j.join_type || "INNER",
    on: (j.on || []).map(c => ({ ...c })),
  }));
  recomputeResolvedTable();
  if (state.alias && state.branch) {
    await loadColumns();
  }
  state.filters = (p.filters || []).map(f => ({ ...f }));
  state.selectedCols = new Set(p.selected_columns || []);
  renderJoins();
  renderColumns();
  renderFilters();
  const addBtn = $("btnAddJoin");
  if (addBtn) addBtn.disabled = state.joins.length >= MAX_JOINS;
  toast(`Modelo "${model.name}" carregado`, "success");
}

$("savedModelsSel").addEventListener("change", () => {
  const sel = $("savedModelsSel");
  $("btnLoadModel").disabled = !sel.value;
  $("btnDeleteModel").disabled = !sel.value;
});

$("btnLoadModel").addEventListener("click", async () => {
  const sel = $("savedModelsSel");
  const model = _findSavedModelById(sel.value);
  if (!model) return;
  await _applyModelToState(model);
});

$("btnSaveModel").addEventListener("click", async () => {
  if (!state.alias || !state.branch) {
    return toast("Escolha tabela e filial antes de salvar", "warning");
  }
  if (state.selectedCols.size === 0) {
    return toast("Selecione ao menos uma coluna", "warning");
  }
  const defaultName = `${state.alias} ${state.branch}` +
    (state.joins.length ? ` (+${state.joins.length} JOINs)` : "");
  const name = prompt("Nome do modelo:", defaultName);
  if (!name || !name.trim()) return;

  await withSpinner($("btnSaveModel"), async () => {
    try {
      const r = await api("/api/saved-queries", { method: "POST",
        body: { name: name.trim(), payload: _snapshotCurrentPayload() },
      });
      await _fetchSavedModels();
      _renderSavedModelsSelect();
      // Pre-seleciona o que acabou de salvar
      $("savedModelsSel").value = String(r.id);
      $("btnLoadModel").disabled = false;
      $("btnDeleteModel").disabled = false;
      toast(`Modelo "${r.name}" salvo no servidor`, "success");
    } catch (e) { toast(e.message, "danger"); }
  }, "Salvando…");
});

$("btnDeleteModel").addEventListener("click", async () => {
  const sel = $("savedModelsSel");
  const model = _findSavedModelById(sel.value);
  if (!model) return;
  if (!confirm(`Excluir modelo "${model.name}"?`)) return;

  await withSpinner($("btnDeleteModel"), async () => {
    try {
      await api(`/api/saved-queries/${model.id}`, { method: "DELETE" });
      await _fetchSavedModels();
      _renderSavedModelsSelect();
      toast("Modelo excluido", "success");
    } catch (e) { toast(e.message, "danger"); }
  }, "Excluindo…");
});

// Sprint 11 — Limpar Formulário: reseta TODO o estado do Builder + grid.
$("btnClearForm").addEventListener("click", () => {
  if (!confirm("Limpar o formulário inteiro (tabela, JOINs, colunas, filtros)?")) return;

  // 1) Estado interno
  state.module = "";
  state.alias = "";
  state.branch = "";
  state.resolvedTable = "";
  state.columnsByAlias = {};
  state.selectedCols = new Set();
  state.filters = [];
  state.joins = [];

  // 2) UI controls
  $("moduleSel").value = "";
  renderAliasOptions();
  $("aliasSel").value = "";
  $("branchSel").innerHTML = `<option value="">—</option>`;
  $("branchSel").disabled = true;
  $("resolvedTable").value = "";
  $("page").value = "1";
  $("psize").value = "100";
  const colSearch = $("column-search");
  if (colSearch) colSearch.value = "";

  // 3) Re-render dos paineis (volta ao estado vazio)
  renderColumns();
  renderFilters();
  renderJoins();

  // 4) Grid e meta
  const grid = document.querySelector("#grid");
  if (grid) {
    grid.querySelector("thead").innerHTML = "";
    grid.querySelector("tbody").innerHTML = "";
  }
  const meta = $("meta");
  if (meta) meta.textContent = "—";
  const pagInd = $("pagIndicator");
  if (pagInd) pagInd.style.display = "none";

  // 5) Botao JOIN desabilita (precisa de tabela base + filial)
  const addJoin = $("btnAddJoin");
  if (addJoin) addJoin.disabled = true;

  toast("Formulário limpo.", "info");
});

// ============================================================
//  Sprint 8 — Skeleton loader (CSS pulsante na tabela)
// ============================================================

function renderSkeleton(cols = 6, rows = 8) {
  const thead = document.querySelector("#grid thead");
  const tbody = document.querySelector("#grid tbody");
  if (!thead || !tbody) return;
  _destroyGridDt();   // evita corromper a instancia DataTables ativa (Sprint 22.3)
  thead.innerHTML =
    `<tr>${Array.from({ length: cols }).map(() =>
       `<th><span class="skeleton-bar" style="width:${50 + Math.floor(Math.random()*40)}%"></span></th>`
     ).join("")}</tr>`;
  tbody.innerHTML = Array.from({ length: rows }).map(() =>
    `<tr>${Array.from({ length: cols }).map(() =>
       `<td><span class="skeleton-bar" style="width:${30 + Math.floor(Math.random()*60)}%"></span></td>`
     ).join("")}</tr>`
  ).join("");
  $("meta").textContent = "⏳ Carregando…";
}

// ---- bootstrap --------------------------------------------------------------
renderJoins();                            // mostra mensagem "sem relacionamentos"
renderSavedModels();                      // Sprint 8
loadAliases();

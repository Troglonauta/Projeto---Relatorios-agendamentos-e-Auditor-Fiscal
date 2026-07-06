import { api, auth, toast, withSpinner, formatBR, loadPublicSettings } from "./api.js";
import { renderLayout } from "./layout.js";

if (!auth.hasAction("schedule")) { location.href = "dashboard.html"; }

renderLayout({ active: "schedules", title: "Agendamentos de Relatórios" });

document.getElementById("page").innerHTML = `
  <div class="d-flex justify-content-between align-items-center mb-3">
    <span class="text-muted">Agende envios automáticos por e-mail. O worker reavalia a cada hora.</span>
    <button class="btn btn-primary" id="btnNew">+ Novo agendamento</button>
  </div>
  <div class="table-card">
    <div class="table-responsive">
      <table class="table mb-0 align-middle">
        <thead><tr>
          <th>Nome</th><th>Tabela</th><th>Formato</th>
          <th>Periodicidade</th><th>Destinatários</th>
          <th>Última execução</th><th>Status</th><th></th>
        </tr></thead>
        <tbody id="rows"><tr><td colspan="8" class="p-3 text-muted">Carregando…</td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- Modal de agendamento -->
  <div class="modal fade" id="schModal" tabindex="-1">
    <div class="modal-dialog modal-lg">
      <form class="modal-content" id="schForm">
        <div class="modal-header">
          <h5 class="modal-title">Novo agendamento</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
        </div>
        <div class="modal-body">
          <div class="row g-2">
            <div class="col-md-6">
              <label class="form-label small">Nome do relatório</label>
              <input id="sName" class="form-control" required placeholder="Ex.: Contas a Receber Diário">
            </div>
            <div class="col-md-3">
              <label class="form-label small">Tabela (Alias)</label>
              <select id="sAlias" class="form-select" required></select>
            </div>
            <div class="col-md-3">
              <label class="form-label small">Filial</label>
              <select id="sBranch" class="form-select" required disabled>
                <option value="">—</option>
              </select>
            </div>

            <!-- Sprint 5: bloco de relacionamentos (JOIN) -->
            <div class="col-md-12 mt-2">
              <div class="d-flex justify-content-between align-items-center mb-1">
                <label class="form-label small mb-0">Relacionamentos (JOIN) — opcional</label>
                <button type="button" class="btn btn-sm btn-outline-primary" id="sAddJoin" disabled>
                  + Adicionar Relacionamento
                </button>
              </div>
              <div id="sJoinsBox"></div>
            </div>

            <div class="col-md-12 mt-2">
              <div class="d-flex justify-content-between align-items-center mb-1">
                <label class="form-label small mb-0">Colunas a exibir</label>
                <span class="text-muted small" id="sColCount"></span>
              </div>
              <!-- Sprint 5: filtro de busca em tempo real -->
              <input id="sColSearch" type="text" class="form-control form-control-sm column-search mb-1"
                     placeholder="🔎 Pesquisar coluna (físico ou descrição)…" autocomplete="off">
              <div id="sCols" class="col-grid" style="max-height:200px">
                <div class="text-muted small p-2">Selecione tabela e filial.</div>
              </div>
            </div>

            <div class="col-md-12 mt-2">
              <div class="d-flex justify-content-between align-items-center">
                <label class="form-label small mb-0">Filtros</label>
                <button type="button" class="btn btn-sm btn-outline-primary" id="sAddFilter">+ Adicionar filtro</button>
              </div>
              <div id="sFilters" class="mt-2"></div>
            </div>

            <div class="col-md-3">
              <label class="form-label small">Formato</label>
              <select id="sFormat" class="form-select">
                <option>xlsx</option><option>csv</option>
                <option>pdf</option><option>ods</option>
              </select>
            </div>
            <div class="col-md-9">
              <label class="form-label small">Destinatários (separar por vírgula)</label>
              <input id="sRecipients" class="form-control" required placeholder="alguem@fertimaxi.com.br, outro@fertimaxi.com.br">
            </div>

            <!-- Periodicidade amigável -->
            <div class="col-md-12">
              <label class="form-label small mt-2">Periodicidade</label>
              <div class="border rounded p-3 soft-panel">
                <div class="row g-2 align-items-end">
                  <div class="col-md-3">
                    <label class="form-label small mb-1">Quando rodar</label>
                    <select id="sFreq" class="form-select">
                      <option value="diario">Diariamente</option>
                      <option value="semanal">Semanalmente</option>
                      <option value="mensal">Mensalmente</option>
                      <option value="unico">Apenas uma vez</option>
                    </select>
                  </div>

                  <div class="col-md-3" id="sWeekdayWrap" hidden>
                    <label class="form-label small mb-1">Dia da semana</label>
                    <select id="sWeekday" class="form-select">
                      <option value="1">Segunda-feira</option>
                      <option value="2">Terça-feira</option>
                      <option value="3">Quarta-feira</option>
                      <option value="4">Quinta-feira</option>
                      <option value="5">Sexta-feira</option>
                      <option value="6">Sábado</option>
                      <option value="0">Domingo</option>
                    </select>
                  </div>

                  <div class="col-md-3" id="sMonthDayWrap" hidden>
                    <label class="form-label small mb-1">Dia do mês</label>
                    <input id="sMonthDay" type="number" min="1" max="31" value="1" class="form-control">
                  </div>

                  <div class="col-md-3" id="sTimeWrap">
                    <label class="form-label small mb-1">Horário (Brasília)</label>
                    <input id="sTime" type="time" class="form-control" value="08:00">
                  </div>

                  <div class="col-md-6" id="sRunAtWrap" hidden>
                    <label class="form-label small mb-1">Data e hora exata</label>
                    <input id="sRunAt" type="datetime-local" class="form-control">
                  </div>
                </div>
                <div class="form-text mt-2">
                  Resumo: <span id="sCronPreview" class="fw-semibold">—</span>
                </div>
              </div>
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancelar</button>
          <button type="submit" class="btn btn-primary" id="sSave">
            <span class="spinner-border spinner-border-sm me-2 d-none" role="status"></span>
            <span class="label">Salvar</span>
          </button>
        </div>
      </form>
    </div>
  </div>
`;

const modal = new bootstrap.Modal("#schModal");

const OPERATORS = [
  { v: "eq",   label: "Igual a" },
  { v: "ne",   label: "Diferente de" },
  { v: "gt",   label: "Maior que" },
  { v: "gte",  label: "Maior ou igual" },
  { v: "lt",   label: "Menor que" },
  { v: "lte",  label: "Menor ou igual" },
  { v: "like", label: "Contém" },
];

const MAX_JOINS_SCH = 5;  // sincronizado com backend (query_engine.DEFAULT_MAX_JOINS)

// Sprint 5 UX polish + Sprint 6: parser BR (DD/MM/YYYY) e ISO (YYYY-MM-DD) -> Protheus (YYYYMMDD)
function _normalizeDateBR_sch(value) {
  if (Array.isArray(value)) return value.map(_normalizeDateBR_sch);
  if (typeof value !== "string") return value;
  const v = value.trim();
  // Sprint 6 — ISO (do input type="date" nativo): YYYY-MM-DD
  const iso = v.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (iso) {
    const [_, yyyy, mm, dd] = iso;
    const d = parseInt(dd,10), mo = parseInt(mm,10);
    if (d < 1 || d > 31 || mo < 1 || mo > 12) return value;
    return `${yyyy}${mm}${dd}`;
  }
  // BR: DD/MM/YYYY ou DD-MM-YYYY
  const m = v.match(/^(\d{2})[\/\-](\d{2})[\/\-](\d{4})$/);
  if (!m) return value;
  const [_, dd, mm, yyyy] = m;
  const d = parseInt(dd,10), mo = parseInt(mm,10);
  if (d < 1 || d > 31 || mo < 1 || mo > 12) return value;
  return `${yyyy}${mm}${dd}`;
}

// Sprint 6 — detecta campos de data para trocar input type="text" → "date"
function _isDateField_sch(field) {
  if (!field) return false;
  const f = field.includes("__") ? field.split("__").pop() : field;
  return /(_EMISSAO|_DTDIGIT|_DATA[A-Z0-9_]*|_VENC[A-Z0-9_]*|_DT[A-Z0-9_]*)$/i.test(f);
}

function _applyDatePicker_sch(inputEl, field) {
  if (!inputEl) return;
  if (_isDateField_sch(field)) {
    inputEl.type = "date";
    inputEl.placeholder = "";
    inputEl.title = "Selecione a data (HTML5)";
  } else {
    inputEl.type = "text";
    inputEl.placeholder = "Valor (datas: dd/mm/aaaa)";
    inputEl.title = "Para campos de data, digite dd/mm/aaaa — o sistema converte para o formato Protheus.";
  }
}

const sched = {
  aliases: [],
  aliasLabel: {},
  tableSuffix: "0",
  alias: "",
  branch: "",
  // Sprint 5: colunas por alias para suportar JOIN
  columnsByAlias: {},          // {"SC5": [...], "SA1": [...]}
  selectedCols: new Set(),     // "SC5.C5_NUM" qualificada (ou nome puro single)
  filters: [],
  joins: [],                   // Sprint 5
};

// =================== Carregar listas =========================================
async function loadAliases() {
  const data = await api("/api/protheus/aliases");
  sched.aliases = data.aliases;
  sched.tableSuffix = data.table_suffix || "0";
  sched.aliasLabel = Object.fromEntries(data.aliases.map(a => [a.alias, a.label]));
  sched._userProfiles = data.user_profiles || [];   // Sprint 5 UX
  document.getElementById("sAlias").innerHTML =
    `<option value="">— escolha —</option>` +
    data.aliases.map(a => `<option value="${a.alias}">${a.alias} — ${a.label}</option>`).join("");
}

async function loadBranches(_aliasIgnored) {
  // Filiais fixas da operacao — vem de /api/settings/public (Fase 4).
  const sel = document.getElementById("sBranch");
  sel.innerHTML = `<option value="">Carregando…</option>`;
  sel.disabled = true;
  const fallback = ["01","02","03","04","05","06","07","08"];
  try {
    const cfg = await loadPublicSettings();
    const branches = Array.isArray(cfg.branches) && cfg.branches.length ? cfg.branches : fallback;
    window._publicSettingsBranches = branches;
    sel.innerHTML = `<option value="">— escolha a filial —</option>` +
      branches.map(b => `<option value="${b}">${b}</option>`).join("");
    sel.disabled = false;
  } catch {
    window._publicSettingsBranches = fallback;
    sel.innerHTML = `<option value="">— escolha a filial —</option>` +
      fallback.map(b => `<option value="${b}">${b}</option>`).join("");
    sel.disabled = false;
  }
}

async function loadColumns() {
  /* Sprint 5: carrega colunas da BASE + JOINs (1 round-trip por tabela). */
  if (!sched.alias || !sched.branch) return;
  const box = document.getElementById("sCols");
  box.innerHTML = `<div class="text-muted small p-2">Carregando colunas…</div>`;
  const targets = [
    { alias: sched.alias, branch: sched.branch },
    ...sched.joins.map(j => ({ alias: j.alias, branch: j.branch })),
  ].filter(t => t.alias && t.branch);

  sched.columnsByAlias = {};
  let firstErr = null;
  for (const t of targets) {
    try {
      const data = await api(`/api/protheus/columns?alias=${t.alias}&branch=${t.branch}`);
      sched.columnsByAlias[t.alias] = data.columns;
    } catch (e) {
      if (!firstErr) firstErr = { alias: t.alias, message: String(e.message || "") };
      sched.columnsByAlias[t.alias] = [];
    }
  }
  if (firstErr && !Object.values(sched.columnsByAlias).some(c => c.length)) {
    box.innerHTML = `<div class="text-danger small p-2"><strong>${firstErr.alias}:</strong> ${firstErr.message}</div>`;
    sched.selectedCols.clear();
    renderFilters();
    return;
  }
  // v2.29 — por padrao, TODAS as colunas ja vem marcadas (mesma qualificacao
  // do renderCols: single = nome puro; com JOIN = ALIAS.coluna).
  sched.selectedCols = new Set();
  for (const alias of Object.keys(sched.columnsByAlias)) {
    for (const c of (sched.columnsByAlias[alias] || [])) {
      sched.selectedCols.add(sched.joins.length ? `${alias}.${c.name}` : c.name);
    }
  }
  renderCols();
  renderFilters();
}

function renderCols() {
  /* Sprint 5: mescla colunas de TODAS as tabelas (base + JOINs) com grupos. */
  const box = document.getElementById("sCols");
  const aliases = Object.keys(sched.columnsByAlias);
  if (!aliases.length) {
    box.innerHTML = `<div class="text-muted small p-2">Nenhuma coluna.</div>`;
    return;
  }
  let total = 0;
  const html = aliases.map(alias => {
    const cols = sched.columnsByAlias[alias] || [];
    total += cols.length;
    const label = sched.aliasLabel[alias] || alias;
    if (!cols.length) return "";
    return `<div class="col-group" data-alias="${alias}">
      <div class="col-group-header">
        <span class="badge bg-secondary me-1">${alias}</span>
        ${label} <span class="text-muted small">(${cols.length})</span>
      </div>
      <div class="col-group-body">
        ${cols.map(c => {
          // Em modo single, mantemos o nome puro; em multi (JOIN), qualificamos.
          const qualified = sched.joins.length ? `${alias}.${c.name}` : c.name;
          const checked = sched.selectedCols.has(qualified);
          const desc = c.description ? c.description.replace(/"/g,"&quot;") : "";
          const searchKey = (`${alias}.${c.name} ${c.name} ${desc}`).toLowerCase();
          return `
            <div class="form-check col-item" data-search="${searchKey}">
              <input class="form-check-input s-col-check" type="checkbox"
                     id="sc_${alias}_${c.name}" value="${qualified}"
                     ${checked ? "checked" : ""}>
              <label class="form-check-label small" for="sc_${alias}_${c.name}" title="${desc}">
                <strong>${c.name}</strong>${desc ? ` <span class="text-muted">— ${desc}</span>` : ''}
              </label>
            </div>`;
        }).join("")}
      </div>
    </div>`;
  }).join("");
  box.innerHTML = html;
  box.querySelectorAll(".s-col-check").forEach(c =>
    c.addEventListener("change", e =>
      e.target.checked ? sched.selectedCols.add(e.target.value) : sched.selectedCols.delete(e.target.value)));
  document.getElementById("sColCount").textContent = `${total} coluna(s)`;
  _applyColSearch();
}

function _applyColSearch() {
  const q = (document.getElementById("sColSearch")?.value || "").trim().toLowerCase();
  const box = document.getElementById("sCols");
  if (!box) return;
  let visible = 0;
  const items = box.querySelectorAll(".col-item[data-search]");
  items.forEach(el => {
    if (!q || el.dataset.search.includes(q)) { el.style.display = ""; visible++; }
    else el.style.display = "none";
  });
  box.querySelectorAll(".col-group[data-alias]").forEach(g => {
    const anyVisible = g.querySelector(".col-item:not([style*='display: none'])");
    g.style.display = anyVisible ? "" : "none";
  });
  const total = items.length;
  document.getElementById("sColCount").textContent = q ? `${visible}/${total} colunas` : `${total} coluna(s)`;
}

function _schAllQualifiedCols() {
  const out = [];
  for (const alias of Object.keys(sched.columnsByAlias)) {
    for (const c of sched.columnsByAlias[alias] || []) {
      const value = sched.joins.length ? `${alias}.${c.name}` : c.name;
      out.push({ value, label: `${value}${c.description ? ' — ' + c.description : ''}` });
    }
  }
  return out;
}

function renderFilters() {
  const box = document.getElementById("sFilters");
  if (!sched.filters.length) { box.innerHTML = `<div class="text-muted small">Sem filtros — relatório completo.</div>`; return; }
  const colOpts = _schAllQualifiedCols().map(c => `<option value="${c.value}">${c.label}</option>`).join("");
  const opOpts  = OPERATORS.map(o => `<option value="${o.v}">${o.label}</option>`).join("");
  box.innerHTML = sched.filters.map((f, i) => `
    <div class="filter-row" data-idx="${i}">
      <select class="form-select form-select-sm f-field"><option value="">— campo —</option>${colOpts}</select>
      <select class="form-select form-select-sm f-op">${opOpts}</select>
      <input class="form-control form-control-sm f-value"
             placeholder="Valor (datas: dd/mm/aaaa)"
             title="Para campos de data, digite dd/mm/aaaa">
      <button type="button" class="btn-remove">✕</button>
    </div>`).join("");
  sched.filters.forEach((f, i) => {
    const row = box.querySelector(`[data-idx="${i}"]`);
    const fld = row.querySelector(".f-field");
    const op  = row.querySelector(".f-op");
    const val = row.querySelector(".f-value");
    fld.value = f.field || "";
    op.value  = f.op || "eq";
    val.value = f.value ?? "";
    // Sprint 6 — Date-picker quando o campo e' de data
    _applyDatePicker_sch(val, f.field);
    fld.addEventListener("change", e => {
      f.field = e.target.value;
      _applyDatePicker_sch(val, f.field);
      val.value = "";
      f.value = "";
    });
    op.addEventListener("change",  e => f.op    = e.target.value);
    val.addEventListener("input",  e => f.value = e.target.value);
    row.querySelector(".btn-remove").addEventListener("click", () => { sched.filters.splice(i, 1); renderFilters(); });
  });
}


// ============================================================
//  Sprint 5 — bloco JOIN no Agendamento
// ============================================================
function renderSchedJoins() {
  const box = document.getElementById("sJoinsBox");
  const addBtn = document.getElementById("sAddJoin");
  if (addBtn) {
    addBtn.disabled = !sched.alias || !sched.branch || sched.joins.length >= MAX_JOINS_SCH;
    addBtn.textContent = sched.joins.length
      ? `+ Adicionar Relacionamento (${sched.joins.length}/${MAX_JOINS_SCH})`
      : "+ Adicionar Relacionamento";
  }
  // Sprint 5 UX polish: indicador dos perfis disponiveis
  const profilesInfo = !auth.isAdmin() && Array.isArray(sched._userProfiles) && sched._userProfiles.length
    ? `<div class="join-profiles-hint small text-muted mb-2">
         🔒 Apenas tabelas dos seus perfis estão disponíveis:
         ${sched._userProfiles.map(p => `<span class="badge bg-info text-dark me-1">${p}</span>`).join("")}
       </div>` : "";
  if (!sched.joins.length) {
    box.innerHTML = `${profilesInfo}<div class="text-muted small">Sem JOINs. Adicione para incluir colunas de outras tabelas no agendamento.</div>`;
    return;
  }
  const usedAliases = new Set([sched.alias, ...sched.joins.map(j => j.alias)]);
  const branches = window._publicSettingsBranches || ["01","02","03","04","05","06","07","08"];
  box.innerHTML = profilesInfo + sched.joins.map((j, idx) => {
    const aliasOpts = sched.aliases
      .filter(a => !usedAliases.has(a.alias) || a.alias === j.alias)
      .map(a => `<option value="${a.alias}" ${a.alias === j.alias ? "selected" : ""}>${a.alias} — ${a.label}</option>`).join("");
    const branchOpts = branches.map(b => `<option value="${b}" ${b === j.branch ? "selected" : ""}>${b}</option>`).join("");
    const previousAliases = [sched.alias, ...sched.joins.slice(0, idx).map(x => x.alias)];
    return `
    <div class="join-card" data-jdx="${idx}">
      <div class="join-card-header">
        <span class="join-badge">${idx + 1}</span>
        <select class="form-select form-select-sm sj-type" style="max-width:280px"
                title="INNER mantém apenas registros da base que casam com a tabela B. LEFT mantém TODOS da base e completa quando houver correspondência.">
          <option value="INNER" ${j.join_type === "INNER" ? "selected" : ""}>Obrigatório ter correspondência (INNER)</option>
          <option value="LEFT"  ${j.join_type === "LEFT"  ? "selected" : ""}>Opcional ter correspondência (LEFT)</option>
        </select>
        <span class="small text-muted">com</span>
        <select class="form-select form-select-sm sj-alias">
          <option value="">— tabela B —</option>${aliasOpts}
        </select>
        <select class="form-select form-select-sm sj-branch" style="max-width:90px">${branchOpts}</select>
        <button type="button" class="btn-remove ms-auto sj-remove">✕</button>
      </div>
      <div class="join-card-body">
        <div class="small text-muted mb-1">Condição ON:</div>
        ${j.on.map((c, ci) => `
          <div class="on-row" data-ci="${ci}">
            <select class="form-select form-select-sm son-left-alias">
              ${previousAliases.map(a => `<option value="${a}" ${a === c.left_alias ? "selected" : ""}>${a}</option>`).join("")}
            </select><span class="small">.</span>
            <select class="form-select form-select-sm son-left-col">
              <option value="">— coluna A —</option>
              ${(sched.columnsByAlias[c.left_alias || previousAliases[0]] || [])
                  .map(co => `<option value="${co.name}" ${co.name === c.left_column ? "selected" : ""}>${co.name}</option>`).join("")}
            </select>
            <span class="small">=</span>
            <span class="small text-muted">${j.alias || "(B)"}</span><span class="small">.</span>
            <select class="form-select form-select-sm son-right-col">
              <option value="">— coluna B —</option>
              ${(sched.columnsByAlias[j.alias] || []).map(co => `<option value="${co.name}" ${co.name === c.right_column ? "selected" : ""}>${co.name}</option>`).join("")}
            </select>
            <button type="button" class="btn-remove son-remove">✕</button>
          </div>`).join("")}
        <button type="button" class="btn btn-link btn-sm ps-0 sj-add-on">+ AND mais uma condição</button>
      </div>
    </div>`;
  }).join("");

  box.querySelectorAll(".join-card").forEach(card => {
    const jdx = parseInt(card.dataset.jdx, 10);
    const j = sched.joins[jdx];
    card.querySelector(".sj-type").addEventListener("change", e => j.join_type = e.target.value);
    card.querySelector(".sj-alias").addEventListener("change", async e => {
      j.alias = e.target.value; await loadColumns(); renderSchedJoins();
    });
    card.querySelector(".sj-branch").addEventListener("change", async e => {
      j.branch = e.target.value; await loadColumns(); renderSchedJoins();
    });
    card.querySelector(".sj-remove").addEventListener("click", async () => {
      sched.joins.splice(jdx, 1); await loadColumns(); renderSchedJoins();
    });
    card.querySelector(".sj-add-on").addEventListener("click", () => {
      j.on.push({ left_alias: sched.alias, left_column: "", right_column: "" });
      renderSchedJoins();
    });
    card.querySelectorAll(".on-row").forEach(row => {
      const ci = parseInt(row.dataset.ci, 10);
      const cond = j.on[ci];
      row.querySelector(".son-left-alias").addEventListener("change", e => { cond.left_alias = e.target.value; renderSchedJoins(); });
      row.querySelector(".son-left-col").addEventListener("change", e => cond.left_column = e.target.value);
      row.querySelector(".son-right-col").addEventListener("change", e => cond.right_column = e.target.value);
      row.querySelector(".son-remove").addEventListener("click", () => {
        if (j.on.length > 1) { j.on.splice(ci, 1); renderSchedJoins(); }
        else toast("Cada JOIN precisa de pelo menos 1 condição ON", "warning");
      });
    });
  });
}

// =================== Periodicidade -> cron ==================================
const FREQ_LABEL = {
  diario:  "Todos os dias",
  semanal: "Toda semana",
  mensal:  "Todo mês",
  unico:   "Uma única vez",
};
const WEEKDAY_LABEL = ["Domingo","Segunda","Terça","Quarta","Quinta","Sexta","Sábado"];

function buildScheduleSpec() {
  const freq = document.getElementById("sFreq").value;
  if (freq === "unico") {
    const dt = document.getElementById("sRunAt").value;
    if (!dt) throw new Error("Informe a data e hora do disparo único.");
    return { cron: null, run_at: new Date(dt).toISOString() };
  }
  const [h, m] = (document.getElementById("sTime").value || "08:00").split(":").map(s => parseInt(s, 10));
  if (Number.isNaN(h) || Number.isNaN(m)) throw new Error("Horário inválido.");

  let cron;
  if (freq === "diario") {
    cron = `${m} ${h} * * *`;
  } else if (freq === "semanal") {
    const dow = document.getElementById("sWeekday").value;
    cron = `${m} ${h} * * ${dow}`;
  } else if (freq === "mensal") {
    const dom = parseInt(document.getElementById("sMonthDay").value || "1", 10);
    cron = `${m} ${h} ${dom} * *`;
  } else {
    throw new Error("Periodicidade inválida.");
  }
  return { cron, run_at: null };
}

function refreshFreqUI() {
  const f = document.getElementById("sFreq").value;
  document.getElementById("sWeekdayWrap").hidden  = (f !== "semanal");
  document.getElementById("sMonthDayWrap").hidden = (f !== "mensal");
  document.getElementById("sTimeWrap").hidden     = (f === "unico");
  document.getElementById("sRunAtWrap").hidden    = (f !== "unico");
  refreshCronPreview();
}

function refreshCronPreview() {
  const f = document.getElementById("sFreq").value;
  const t = document.getElementById("sTime").value || "08:00";
  let txt = "";
  if (f === "diario")  txt = `Todos os dias às ${t} (Brasília)`;
  if (f === "semanal") txt = `Toda ${WEEKDAY_LABEL[+document.getElementById("sWeekday").value]} às ${t}`;
  if (f === "mensal")  txt = `Todo dia ${document.getElementById("sMonthDay").value} do mês às ${t}`;
  if (f === "unico") {
    const dt = document.getElementById("sRunAt").value;
    txt = dt ? `Uma única vez em ${formatBR(dt)}` : "Uma única vez (informe data e hora)";
  }
  document.getElementById("sCronPreview").textContent = txt;
}

["sFreq","sWeekday","sMonthDay","sTime","sRunAt"].forEach(id =>
  document.getElementById(id).addEventListener("change", refreshFreqUI));
document.getElementById("sFreq").addEventListener("change", refreshFreqUI);

// =================== Eventos do builder no modal ============================
document.getElementById("sAlias").addEventListener("change", async e => {
  sched.alias = e.target.value;
  sched.branch = "";
  sched.columnsByAlias = {}; sched.selectedCols.clear(); sched.filters = [];
  sched.joins = [];
  renderCols(); renderFilters(); renderSchedJoins();
  if (sched.alias) await loadBranches(sched.alias);
});

document.getElementById("sBranch").addEventListener("change", async e => {
  sched.branch = e.target.value;
  if (sched.alias && sched.branch) { await loadColumns(); renderSchedJoins(); }
});

document.getElementById("sAddFilter").addEventListener("click", () => {
  sched.filters.push({ field: "", op: "eq", value: "" });
  renderFilters();
});

// Sprint 5 — bloco JOIN + filtro de busca
document.getElementById("sAddJoin").addEventListener("click", async () => {
  if (!sched.alias || !sched.branch) return toast("Escolha tabela e filial primeiro", "warning");
  if (sched.joins.length >= MAX_JOINS_SCH) {
    return toast(`Limite de ${MAX_JOINS_SCH} relacionamentos atingido — proteção do banco`, "warning");
  }
  sched.joins.push({
    alias: "", branch: sched.branch, join_type: "INNER",
    on: [{ left_alias: sched.alias, left_column: "", right_column: "" }],
  });
  renderSchedJoins();
});

document.getElementById("sColSearch").addEventListener("input", _applyColSearch);
document.getElementById("sColSearch").addEventListener("keydown", e => {
  if (e.key === "Escape") { e.target.value = ""; _applyColSearch(); }
});

// =================== Lista de agendamentos ==================================
async function reload() {
  const list = await api("/api/schedules");
  const rows = document.getElementById("rows");
  if (!list.length) { rows.innerHTML = `<tr><td colspan="8" class="p-3 text-muted">Nenhum agendamento</td></tr>`; return; }
  rows.innerHTML = list.map(s => `
    <tr>
      <td><strong>${s.name}</strong></td>
      <td>${s.table_name}</td>
      <td>${s.file_format}</td>
      <td><small>${cronHumanize(s.cron) || (s.run_at ? `Único: ${formatBR(s.run_at)}` : "-")}</small></td>
      <td><small>${s.recipients}</small></td>
      <td><small>${s.last_run_at ? formatBR(s.last_run_at) : "-"}</small></td>
      <td>
        ${s.last_status === "success" ? '<span class="badge bg-success">OK</span>'
          : s.last_status === "error" ? `<span class="badge bg-danger" title="${(s.last_error||'').replace(/"/g,'&quot;')}">ERRO</span>`
          : '<span class="badge bg-secondary">—</span>'}
        ${s.is_active ? '' : ' <span class="badge bg-warning text-dark">pausado</span>'}
      </td>
      <td class="text-end">
        <button class="btn btn-sm btn-outline-primary me-1" data-run="${s.id}">
          <span class="spinner-border spinner-border-sm me-1 d-none" role="status"></span>
          <span class="label">Rodar agora</span>
        </button>
        <button class="btn btn-sm btn-outline-secondary me-1" data-toggle="${s.id}">${s.is_active ? "Pausar" : "Ativar"}</button>
        <button class="btn btn-sm btn-outline-danger" data-del="${s.id}">Excluir</button>
      </td>
    </tr>`).join("");

  rows.querySelectorAll("[data-run]").forEach(b => b.onclick = async () => {
    await withSpinner(b, async () => {
      try {
        const r = await api(`/api/schedules/${b.dataset.run}/run-now`, { method: "POST" });
        if (r.status === "queued") {
          toast(`Agendamento enfileirado (job ${r.job_id.slice(0,8)}). Acompanhe o status na lista.`, "success");
        } else if (r.status === "success") {
          toast("Relatório enviado por e-mail", "success");
        } else {
          toast("Falha: " + (r.detail || r.error), "danger");
        }
        reload();
      } catch (e) { toast(e.message, "danger"); }
    }, "Enfileirando…");
  });
  rows.querySelectorAll("[data-toggle]").forEach(b => b.onclick = async () => {
    try { await api(`/api/schedules/${b.dataset.toggle}/toggle`, { method: "POST" }); reload(); }
    catch (e) { toast(e.message, "danger"); }
  });
  rows.querySelectorAll("[data-del]").forEach(b => b.onclick = async () => {
    if (!confirm("Excluir agendamento?")) return;
    try { await api(`/api/schedules/${b.dataset.del}`, { method: "DELETE" }); reload(); }
    catch (e) { toast(e.message, "danger"); }
  });
}

// =================== Cron -> humano (só pra exibir na lista) ================
function cronHumanize(c) {
  if (!c) return null;
  const p = c.split(/\s+/);
  if (p.length !== 5) return c;
  const [m, h, dom, mon, dow] = p;
  const time = `${h.padStart(2,"0")}:${m.padStart(2,"0")}`;
  if (dom === "*" && mon === "*" && dow === "*") return `Diário às ${time}`;
  if (dom === "*" && mon === "*" && dow !== "*") return `${WEEKDAY_LABEL[+dow] || dow} às ${time}`;
  if (mon === "*" && dow === "*" && dom !== "*") return `Dia ${dom} do mês às ${time}`;
  return c;
}

// =================== Reset / open ============================================
function openCreate() {
  document.getElementById("sName").value = "";
  document.getElementById("sFormat").value = "xlsx";
  document.getElementById("sRecipients").value = "";
  document.getElementById("sFreq").value = "diario";
  document.getElementById("sTime").value = "08:00";
  document.getElementById("sWeekday").value = "1";
  document.getElementById("sMonthDay").value = "1";
  document.getElementById("sRunAt").value = "";
  sched.alias = ""; sched.branch = "";
  sched.columnsByAlias = {}; sched.selectedCols.clear(); sched.filters = [];
  sched.joins = [];
  document.getElementById("sAlias").value = "";
  document.getElementById("sBranch").innerHTML = `<option value="">—</option>`;
  document.getElementById("sBranch").disabled = true;
  document.getElementById("sColSearch").value = "";
  renderCols(); renderFilters(); renderSchedJoins(); refreshFreqUI();
  modal.show();
}

document.getElementById("btnNew").onclick = openCreate;

document.getElementById("schForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = document.getElementById("sSave");
  await withSpinner(btn, async () => {
    let when;
    try { when = buildScheduleSpec(); } catch (err) { return toast(err.message, "warning"); }
    if (!sched.alias) return toast("Selecione a tabela (Alias).", "warning");
    if (!sched.branch) return toast("Selecione a filial — obrigatória para o agendamento.", "warning");
    const recipients = document.getElementById("sRecipients").value
      .split(",").map(s => s.trim()).filter(Boolean);
    if (!recipients.length) return toast("Informe ao menos um destinatário.", "warning");

    // Sprint 5 — empacota JOINs e valida
    const joinsOut = [];
    for (let i = 0; i < sched.joins.length; i++) {
      const j = sched.joins[i];
      if (!j.alias)  return toast(`Relacionamento #${i+1}: escolha a tabela B`, "warning");
      if (!j.branch) return toast(`Relacionamento #${i+1}: escolha a filial B`, "warning");
      for (let ci = 0; ci < j.on.length; ci++) {
        const c = j.on[ci];
        if (!c.left_alias || !c.left_column || !c.right_column) {
          return toast(`Relacionamento #${i+1}: complete a condição ON #${ci+1}`, "warning");
        }
      }
      joinsOut.push({
        alias: j.alias, branch: j.branch, join_type: j.join_type || "INNER",
        on: j.on.map(c => ({
          left_alias: c.left_alias, left_column: c.left_column,
          right_column: c.right_column,
        })),
      });
    }
    if (joinsOut.length && sched.selectedCols.size === 0) {
      return toast("Com JOIN ativo, escolha ao menos uma coluna", "warning");
    }

    const payload = {
      name: document.getElementById("sName").value,
      alias: sched.alias,
      branch: sched.branch,
      columns: [...sched.selectedCols],
      rules: sched.filters.filter(r => r.field).map(r => ({
        field: r.field, op: r.op || "eq", value: _normalizeDateBR_sch(r.value)
      })),
      file_format: document.getElementById("sFormat").value,
      recipients,
      cron: when.cron,
      run_at: when.run_at,
      joins: joinsOut.length ? joinsOut : undefined,
    };
    try {
      await api("/api/schedules", { method: "POST", body: payload });
      modal.hide();
      reload();
      toast("Agendamento criado", "success");
    } catch (err) { toast(err.message, "danger"); }
  }, "Salvando…");
});

(async () => { await loadAliases(); await reload(); })();

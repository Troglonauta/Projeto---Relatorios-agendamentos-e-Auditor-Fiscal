/* Dashboard remodelado (Fase 3 Sprint 3):
   - KPIs do dia (relatórios gerados, falhas, jobs ativos)
   - Sparkline 7 dias
   - Donut fiscal (OK x Anomalia x Crítica)
   - Feed cronológico reverso (jobs + anomalias)
*/
import { api, auth, toast, formatBR } from "./api.js";
import { renderLayout } from "./layout.js";
import { getTheme } from "./theme.js";

// Defesa em profundidade — operador nao acessa Dashboard nem por URL direta.
if (!auth.isAdmin()) { location.href = "protheus.html"; }

renderLayout({ active: "dashboard", title: "Dashboard" });

const $ = (id) => document.getElementById(id);

document.getElementById("page").innerHTML = `
  <!-- Sprint 19 — KPIs orientados a DOCUMENTOS (fiscal) -->
  <div class="kpi-grid mb-3">
    <div class="kpi-card" title="Total de notas auditadas nos últimos 30 dias">
      <div class="label">📋 Notas Auditadas</div>
      <div class="value" id="kpiNotasAuditadas">—</div>
    </div>
    <div class="kpi-card" title="Notas com ao menos 1 divergência pendente">
      <div class="label">⚠ Notas com Divergência</div>
      <div class="value text-danger" id="kpiNotasDiv">—</div>
    </div>
    <div class="kpi-card" title="Notas auditadas sem divergências (em conformidade)">
      <div class="label">✓ Notas OK</div>
      <div class="value text-success" id="kpiNotasOk">—</div>
    </div>
    <div class="kpi-card" title="% de conformidade fiscal (notas OK / total)">
      <div class="label">📊 Conformidade</div>
      <div class="value" id="kpiConformidade">—</div>
    </div>
  </div>

  <!-- KPIs operacionais (jobs/relatorios) — secundários -->
  <div class="kpi-grid mb-3">
    <div class="kpi-card"><div class="label">Relatórios hoje</div><div class="value" id="kpiReports">—</div></div>
    <div class="kpi-card"><div class="label">Jobs ativos</div><div class="value" id="kpiActive">—</div></div>
    <div class="kpi-card"><div class="label">Falhas hoje</div><div class="value text-danger" id="kpiFail">—</div></div>
    <div class="kpi-card"><div class="label">Usuário</div><div class="value">${auth.user?.username || "-"}</div></div>
  </div>

  <div class="row g-3 mb-3">
    <div class="col-lg-7">
      <div class="table-card p-3">
        <h6 class="text-muted text-uppercase mb-3" style="font-size:12px">Relatórios concluídos · últimos 7 dias</h6>
        <canvas id="chartSpark" height="120"></canvas>
      </div>
    </div>
    <div class="col-lg-5">
      <div class="table-card p-3">
        <h6 class="text-muted text-uppercase mb-3" style="font-size:12px">Auditorias fiscais recentes</h6>
        <canvas id="chartFiscal" height="120"></canvas>
        <div class="small text-muted mt-2" id="fiscalNote">—</div>
      </div>
    </div>
  </div>

  <!-- Sprint 8 Part 3 — Anomalias por severidade (doughnut) + Evolução 30d (bars) -->
  <div class="row g-3 mb-3">
    <div class="col-lg-5">
      <div class="dashboard-chart-card h-100">
        <div class="d-flex justify-content-between align-items-center mb-2">
          <h6 class="m-0">🥧 Anomalias por severidade</h6>
          <div class="small text-muted" id="sevChartNote">—</div>
        </div>
        <div class="dashboard-chart-canvas-wrap">
          <canvas id="chartSeverity"></canvas>
        </div>
      </div>
    </div>
    <div class="col-lg-7">
      <div class="dashboard-chart-card h-100">
        <div class="d-flex justify-content-between align-items-center mb-2">
          <h6 class="m-0">📊 Evolução de anomalias — últimos 30 dias</h6>
          <div class="small text-muted" id="anomChartNote">—</div>
        </div>
        <div class="dashboard-chart-canvas-wrap">
          <canvas id="chartAnomalies30d"></canvas>
        </div>
      </div>
    </div>
  </div>

  <div class="table-card mt-3">
    <div class="table-toolbar">
      <span class="text-muted small">Feed cronológico — sucessos e anomalias recentes</span>
    </div>
    <div class="table-responsive" style="max-height:480px">
      <table class="table table-sm align-middle mb-0">
        <thead><tr>
          <th style="width:160px">Quando</th>
          <th style="width:90px">Tipo</th>
          <th>Detalhe</th>
          <th style="width:100px">Severidade</th>
        </tr></thead>
        <tbody id="feedRows"><tr><td colspan="4" class="p-3 text-muted">Carregando…</td></tr></tbody>
      </table>
    </div>
  </div>
`;

const SEV_BADGE = {
  critical: '<span class="badge bg-danger">crítica</span>',
  warn:     '<span class="badge bg-warning text-dark">aviso</span>',
  info:     '<span class="badge bg-info">info</span>',
};

// ============================================================
//  Sprint 8 Part 3 — Theme-aware Chart.js
//
//  Chart.js nao reage a `body.dark-mode` automaticamente. Quando o usuario
//  troca o tema, destruimos as instancias e re-renderizamos com as cores
//  certas (eixos, grid, legend).
// ============================================================

const _chartRegistry = new Map();   // id -> Chart instance
const _chartFactories = new Map();  // id -> () => Chart  (para re-render)

function _themeColors() {
  /* Lê as variaveis CSS efetivas do tema atual (so chamada apos render). */
  const cs = getComputedStyle(document.body);
  return {
    text:   cs.getPropertyValue("--text").trim()   || "#1f2d3d",
    muted:  cs.getPropertyValue("--muted").trim()  || "#6c757d",
    border: cs.getPropertyValue("--border").trim() || "#dde3e8",
    isDark: getTheme() === "dark",
  };
}

function _applyThemeDefaults() {
  /* Aplica defaults globais do Chart.js conforme tema. Chamado antes de cada
     `new Chart(...)`. */
  if (typeof Chart === "undefined") return;
  const c = _themeColors();
  Chart.defaults.color = c.text;
  Chart.defaults.borderColor = c.border;
  // Grid lines mais sutis no dark
  Chart.defaults.scale.grid = Chart.defaults.scale.grid || {};
  Chart.defaults.scale.grid.color = c.isDark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.06)";
}

function _renderChart(id, factory) {
  /* Wrapper: cria via factory(), registra para re-render em mudanca de tema. */
  _chartFactories.set(id, factory);
  // Destroi instancia anterior se existir
  const prev = _chartRegistry.get(id);
  if (prev) try { prev.destroy(); } catch {}
  _applyThemeDefaults();
  const chart = factory();
  _chartRegistry.set(id, chart);
  return chart;
}

function _rerenderAllCharts() {
  /* Recria todas as charts ao trocar tema — modo mais simples e robusto que
     mutar opcoes manualmente (Chart.js nao reage bem a alteracoes profundas). */
  for (const [id, factory] of _chartFactories.entries()) {
    const prev = _chartRegistry.get(id);
    if (prev) try { prev.destroy(); } catch {}
    _applyThemeDefaults();
    _chartRegistry.set(id, factory());
  }
}

document.addEventListener("theme:changed", _rerenderAllCharts);

async function loadKpisAndSpark() {
  try {
    const t = await api("/api/dashboard/today");
    $("kpiReports").textContent = t.reports.total;
    $("kpiActive").textContent = (t.jobs.queued + t.jobs.running);
    $("kpiFail").textContent = t.reports.failed;

    _renderChart("chartSpark", () => new Chart($("chartSpark"), {
      type: "line",
      data: {
        labels: t.sparkline_7d.map(d => d.date.slice(5)),
        datasets: [{
          label: "Concluídos",
          data: t.sparkline_7d.map(d => d.count),
          borderColor: "#2E8B3D",
          backgroundColor: "rgba(46,139,61,0.12)",
          fill: true,
          tension: 0.3,
        }],
      },
      options: {
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
      },
    }));
  } catch (e) { /* silencioso */ }
}

async function loadFiscal() {
  try {
    const r = await api("/api/dashboard/fiscal-recent?limit=20");
    const ok       = r.items.filter(i => i.outcome === "ok").length;
    const anomaly  = r.items.filter(i => i.outcome === "anomaly").length;
    const critical = r.items.reduce((s, i) => s + (i.critical || 0), 0);
    const other    = r.items.length - ok - anomaly;

    _renderChart("chartFiscal", () => new Chart($("chartFiscal"), {
      type: "doughnut",
      data: {
        labels: ["OK", "Com anomalia", "Outros"],
        datasets: [{
          data: [ok, anomaly, other],
          backgroundColor: ["#2E8B3D", "#C0392B", "#9aa5b1"],
          borderColor: _themeColors().border,
        }],
      },
      options: { plugins: { legend: { position: "bottom" } } },
    }));
    $("fiscalNote").textContent = r.items.length
      ? `${r.items.length} auditoria(s) recentes — ${critical} divergências críticas no total`
      : "Nenhuma auditoria fiscal registrada ainda.";
  } catch (e) {
    $("fiscalNote").textContent = "Auditor Fiscal não disponível ou sem permissão.";
  }
}

// ============================================================
//  Sprint 8 Part 3 — Doughnut "Anomalias por Severidade"
//  Alimenta-se de /api/fiscal/summary (que ja existe)
// ============================================================
async function loadSeverityDoughnut() {
  try {
    const s = await api("/api/fiscal/summary?days=30");
    // Sprint 19 — atualiza os 4 KPIs orientados a documentos
    const totalDocs = s.total_docs_audited || 0;
    const docsErr   = s.docs_with_errors  || 0;
    const docsOk    = s.docs_ok           || 0;
    const conf      = totalDocs > 0 ? ((docsOk / totalDocs) * 100).toFixed(1) + "%" : "—";
    const kpi1 = $("kpiNotasAuditadas"); if (kpi1) kpi1.textContent = totalDocs;
    const kpi2 = $("kpiNotasDiv");       if (kpi2) kpi2.textContent = docsErr;
    const kpi3 = $("kpiNotasOk");        if (kpi3) kpi3.textContent = docsOk;
    const kpi4 = $("kpiConformidade");   if (kpi4) kpi4.textContent = conf;

    // /summary devolve {total, critical, warn, info, pending?, by_branch}
    const critical = s.critical || 0;
    const warn     = s.warn || 0;
    const info     = s.info || 0;
    const pending  = s.pending || 0;
    const total    = critical + warn + info + pending;

    if (total === 0) {
      $("sevChartNote").textContent = "Sem anomalias nos últimos 30 dias — 🎉 compliance OK";
      // Renderiza placeholder vazio para o layout nao colapsar
      _renderChart("chartSeverity", () => new Chart($("chartSeverity"), {
        type: "doughnut",
        data: { labels: ["Sem dados"], datasets: [{ data: [1], backgroundColor: [_themeColors().border] }] },
        options: { plugins: { legend: { display: false } } },
      }));
      return;
    }

    _renderChart("chartSeverity", () => new Chart($("chartSeverity"), {
      type: "doughnut",
      data: {
        labels: ["Crítica", "Aviso", "Info", "Pendente"],
        datasets: [{
          data: [critical, warn, info, pending],
          backgroundColor: ["#C0392B", "#F2C037", "#2E8B3D", "#9aa5b1"],
          borderColor: _themeColors().border,
          borderWidth: 2,
        }],
      },
      options: {
        maintainAspectRatio: false,
        plugins: {
          legend: { position: "bottom" },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const pct = total ? ((ctx.parsed / total) * 100).toFixed(1) : 0;
                return `${ctx.label}: ${ctx.parsed} (${pct}%)`;
              },
            },
          },
        },
      },
    }));
    $("sevChartNote").textContent =
      `${total} no total · ${critical} crítica${critical === 1 ? "" : "s"} (${total ? ((critical/total)*100).toFixed(0) : 0}%)`;
  } catch (e) {
    $("sevChartNote").textContent = "Auditor Fiscal indisponível.";
  }
}

async function loadFeed() {
  try {
    const r = await api("/api/dashboard/feed?limit=50");
    const tb = $("feedRows");
    if (!r.items.length) { tb.innerHTML = `<tr><td colspan="4" class="p-3 text-muted">Sem eventos recentes</td></tr>`; return; }
    tb.innerHTML = r.items.map(i => `
      <tr>
        <td><small>${formatBR(i.ts)}</small></td>
        <td><span class="badge bg-secondary">${i.kind || i.type}</span></td>
        <td><strong>${i.title}</strong>${i.detail ? `<br><small class="text-muted">${i.detail}</small>` : ""}</td>
        <td>${SEV_BADGE[i.severity] || `<span class="badge bg-secondary">${i.severity}</span>`}</td>
      </tr>`).join("");
  } catch (e) { toast("Falha no feed: " + e.message, "danger"); }
}

// ============================================================
//  Sprint 8 — Gráfico de anomalias por dia (Chart.js stacked bars)
// ============================================================
async function loadAnomaliesHistogram() {
  try {
    const r = await api("/api/dashboard/fiscal-anomalies-histogram?days=30");
    const labels   = r.series.map(d => d.date.slice(5));   // "MM-DD"
    const critical = r.series.map(d => d.critical);
    const warn     = r.series.map(d => d.warn);
    const info     = r.series.map(d => d.info);
    const totalAll = r.series.reduce((s, d) => s + d.total, 0);
    const totalCrit = critical.reduce((s, v) => s + v, 0);

    _renderChart("chartAnomalies30d", () => new Chart($("chartAnomalies30d"), {
      type: "bar",
      data: {
        labels,
        datasets: [
          { label: "Críticas", data: critical, backgroundColor: "#C0392B" },
          { label: "Aviso",    data: warn,     backgroundColor: "#F2C037" },
          { label: "Info",     data: info,     backgroundColor: "#9aa5b1" },
        ],
      },
      options: {
        maintainAspectRatio: false,
        plugins: {
          legend: { position: "bottom" },
          tooltip: { mode: "index", intersect: false },
        },
        scales: {
          x: { stacked: true, ticks: { autoSkip: true, maxTicksLimit: 15 } },
          y: { stacked: true, beginAtZero: true, ticks: { precision: 0 } },
        },
      },
    }));
    $("anomChartNote").textContent =
      `${totalAll} anomalia(s) nos últimos ${r.days} dias · ${totalCrit} críticas`;
  } catch (e) {
    $("anomChartNote").textContent = "Não foi possível carregar o histórico de anomalias.";
  }
}

(async () => {
  await Promise.all([
    loadKpisAndSpark(),
    loadFiscal(),
    loadFeed(),
    loadAnomaliesHistogram(),   // Sprint 8
    loadSeverityDoughnut(),     // Sprint 8 Part 3
  ]);
})();

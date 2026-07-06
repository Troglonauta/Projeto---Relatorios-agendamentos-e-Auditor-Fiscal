import { api, auth, toast } from "./api.js";
import { renderLayout } from "./layout.js";

if (!auth.isAdmin()) { location.href = "dashboard.html"; }

renderLayout({ active: "audit", title: "Trilha de Auditoria" });

document.getElementById("page").innerHTML = `
  <div class="table-card p-3 mb-3">
    <div class="row g-2">
      <div class="col-md-3"><label class="form-label small">Usuário</label>
        <input id="fUser" class="form-control"></div>
      <div class="col-md-3"><label class="form-label small">Ação contém</label>
        <input id="fAction" class="form-control" placeholder="auth, query, schedule, fiscal.document.acked…"></div>
      <div class="col-md-2"><label class="form-label small">Sucesso?</label>
        <select id="fSuccess" class="form-select">
          <option value="">Todos</option><option value="true">Sim</option><option value="false">Não</option>
        </select></div>
      <div class="col-md-2"><label class="form-label small">Desde</label>
        <input id="fSince" type="datetime-local" class="form-control"></div>
      <div class="col-md-2 d-flex align-items-end">
        <button class="btn btn-primary w-100" id="btnFilter">Filtrar</button>
      </div>
    </div>
    <!-- Sprint 19 — atalhos para auditar tomadas de decisao fiscais -->
    <div class="mt-2 d-flex gap-2 flex-wrap small">
      <span class="text-muted">Atalhos:</span>
      <button class="btn btn-sm btn-outline-success" data-quick="fiscal.document.acked"
              title="Decisões fiscais — 'marcar como ciente'">
        📝 Tomadas de decisão fiscal
      </button>
      <button class="btn btn-sm btn-outline-warning" data-quick="fiscal.anomaly.snooze">
        ⏰ Snoozes
      </button>
      <button class="btn btn-sm btn-outline-secondary" data-quick="fiscal.">
        🗂 Tudo fiscal
      </button>
      <button class="btn btn-sm btn-outline-secondary" data-quick="">
        ↺ Limpar
      </button>
    </div>
  </div>

  <div class="table-card">
    <div class="table-responsive">
      <table class="table table-sm mb-0">
        <thead><tr>
          <th style="width:160px">Quando</th>
          <th>Usuário</th>
          <th>Ação</th>
          <th>Detalhe</th>
          <th style="width:120px">IP</th>
          <th style="width:70px">OK?</th>
        </tr></thead>
        <tbody id="rows"><tr><td colspan="6" class="p-3 text-muted">Carregando…</td></tr></tbody>
      </table>
    </div>
  </div>
`;

// Sprint 19 — badges visuais por categoria de acao (foco em tomadas de decisao)
const ACTION_BADGES = {
  "fiscal.document.acked": {
    label: "📝 Decisão Fiscal",
    cls:   "bg-success",
    title: "Documento marcado como ciente (com justificação registrada)",
  },
  "fiscal.anomaly.ack": {
    label: "✓ Ack",
    cls:   "bg-success",
    title: "Anomalia individual marcada como ciente",
  },
  "fiscal.anomaly.snooze": {
    label: "⏰ Snooze",
    cls:   "bg-warning text-dark",
    title: "Anomalia silenciada temporariamente",
  },
  "fiscal.anomaly.unack": {
    label: "↶ Unack",
    cls:   "bg-info text-dark",
    title: "Decisão revertida — anomalia reabriu",
  },
};

function _actionCell(action) {
  const meta = ACTION_BADGES[action];
  if (meta) {
    return `<span class="badge ${meta.cls}" title="${meta.title}">${meta.label}</span> ` +
           `<code class="small">${action}</code>`;
  }
  return `<code>${action}</code>`;
}

function _highlightDecision(detail) {
  // Destaca o "Justificacao/Decisao: ..." visualmente no detalhe da trilha
  if (!detail) return "";
  const escaped = detail.replace(/[<>]/g, c => ({"<":"&lt;",">":"&gt;"})[c]);
  return escaped.replace(
    /(Justifica(?:c|ç)(?:a|ã)o\/Decis(?:a|ã)o:)\s*([^.]*?)(\.|$)/i,
    (_, lead, body, tail) => {
      const safeBody = (body || "").trim();
      const cls = /sem justifica/i.test(safeBody) ? "text-warning" : "text-success fw-semibold";
      return `<strong>${lead}</strong> <span class="${cls}">${safeBody || "(vazio)"}</span>${tail}`;
    },
  );
}

async function load() {
  const params = new URLSearchParams();
  const u = document.getElementById("fUser").value.trim();
  const a = document.getElementById("fAction").value.trim();
  const s = document.getElementById("fSuccess").value;
  const since = document.getElementById("fSince").value;
  if (u) params.set("username", u);
  if (a) params.set("action", a);
  if (s) params.set("success", s);
  if (since) params.set("since", new Date(since).toISOString());
  try {
    const list = await api(`/api/audit?${params.toString()}`);
    const rows = document.getElementById("rows");
    if (!list.length) {
      rows.innerHTML = `<tr><td colspan="6" class="p-3 text-muted">Nenhum evento</td></tr>`;
      return;
    }
    rows.innerHTML = list.map(l => {
      // Sprint 19: linha inteira realça quando for tomada de decisao fiscal
      const isDecisao = /^fiscal\.document\.acked$/.test(l.action || "");
      const rowCls = isDecisao ? "table-success" : "";
      return `
        <tr class="${rowCls}">
          <td><small>${new Date(l.timestamp).toLocaleString()}</small></td>
          <td>${l.username || "-"}</td>
          <td>${_actionCell(l.action || "")}</td>
          <td><small>${_highlightDecision(l.detail || "")}</small></td>
          <td><small>${l.ip_address || "-"}</small></td>
          <td>${l.success ? '<span class="badge bg-success">OK</span>' : '<span class="badge bg-danger">FALHA</span>'}</td>
        </tr>`;
    }).join("");
  } catch (e) { toast(e.message, "danger"); }
}

document.getElementById("btnFilter").onclick = load;

// Sprint 19 — quick filters (atalhos)
document.querySelectorAll("button[data-quick]").forEach(btn => {
  btn.addEventListener("click", () => {
    document.getElementById("fAction").value = btn.dataset.quick;
    load();
  });
});

load();

import { api, auth, toast, formatBR } from "./api.js";
import { renderLayout } from "./layout.js";

if (!auth.isAdmin()) { location.href = "dashboard.html"; }

renderLayout({ active: "users", title: "Usuários e Permissões" });

document.getElementById("page").innerHTML = `
  <div class="d-flex justify-content-between align-items-center mb-3">
    <span class="text-muted">Gestão de usuários, papéis e permissões granulares.</span>
    <div class="d-flex align-items-center gap-3">
      <div class="form-check form-switch mb-0">
        <input class="form-check-input" type="checkbox" id="chkShowInactive">
        <label class="form-check-label small" for="chkShowInactive">Mostrar inativos</label>
      </div>
      <button class="btn btn-primary" id="btnNew">+ Novo usuário</button>
    </div>
  </div>
  <div class="table-card">
    <div class="table-responsive">
      <table class="table mb-0 align-middle">
        <thead>
          <tr>
            <th>#</th><th>Usuário</th><th>Nome</th><th>Email</th><th>Papel</th>
            <th>Perfis</th><th>Ações</th><th>Criado em</th><th>Criado por</th><th>Status</th><th></th>
          </tr>
        </thead>
        <tbody id="rows"><tr><td colspan="11" class="p-3 text-muted">Carregando…</td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- Modal create/edit -->
  <div class="modal fade" id="userModal" tabindex="-1">
    <div class="modal-dialog modal-lg">
      <form class="modal-content" id="userForm">
        <div class="modal-header">
          <h5 class="modal-title" id="userModalTitle">Novo usuário</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
        </div>
        <div class="modal-body">
          <input type="hidden" id="uId">
          <div class="row g-2">
            <div class="col-md-4"><label class="form-label">Usuário</label>
              <input id="uUsername" class="form-control" required></div>
            <div class="col-md-4"><label class="form-label">Email</label>
              <input id="uEmail" type="email" class="form-control" required></div>
            <div class="col-md-4"><label class="form-label">Nome completo</label>
              <input id="uFullName" class="form-control"></div>
            <div class="col-md-4" id="pwdWrap">
              <label class="form-label">Senha</label>
              <input id="uPassword" type="password" class="form-control" minlength="8" autocomplete="new-password">
            </div>
            <div class="col-md-4" id="pwdWrap2">
              <label class="form-label">Confirmar Senha</label>
              <input id="uPassword2" type="password" class="form-control" minlength="8" autocomplete="new-password">
            </div>
            <div class="col-md-4"><label class="form-label">Papel</label>
              <select id="uRole" class="form-select">
                <option value="operator">Operador</option>
                <option value="admin">Administrador</option>
              </select></div>
            <div class="col-md-4"><label class="form-label">Ativo</label>
              <select id="uActive" class="form-select">
                <option value="true">Sim</option><option value="false">Não</option>
              </select></div>
            <div class="col-md-12">
              <label class="form-label">Perfis vinculados <span class="text-muted small">(o usuário acessa as tabelas dos perfis marcados)</span></label>
              <div id="uProfiles" class="border rounded p-2 soft-panel" style="max-height:140px;overflow:auto">
                <div class="text-muted small">Carregando perfis…</div>
              </div>
            </div>
            <!-- Hotfix v2.0: multi-select dinamico em vez de texto livre -->
            <div class="col-md-12">
              <label class="form-label">
                Tabelas extras <span class="text-muted small">— opcional, complementa os perfis</span>
              </label>
              <select id="uTables" class="form-select" multiple size="5"></select>
              <div class="form-text">
                Ctrl/Cmd+click seleciona múltiplas. Lista carregada de
                <code>/api/protheus/aliases</code>. Vazio = só os perfis acima.
              </div>
            </div>
            <div class="col-md-12">
              <label class="form-label">Ações permitidas</label><br>
              <div class="form-check form-check-inline">
                <input class="form-check-input act" type="checkbox" value="view" id="actView">
                <label class="form-check-label" for="actView">Visualizar</label>
              </div>
              <div class="form-check form-check-inline">
                <input class="form-check-input act" type="checkbox" value="export" id="actExport">
                <label class="form-check-label" for="actExport">Exportar</label>
              </div>
              <div class="form-check form-check-inline">
                <input class="form-check-input act" type="checkbox" value="schedule" id="actSchedule">
                <label class="form-check-label" for="actSchedule">Agendar envios</label>
              </div>
              <div class="form-check form-check-inline">
                <input class="form-check-input act" type="checkbox" value="fiscal" id="actFiscal">
                <label class="form-check-label" for="actFiscal">Auditor Fiscal</label>
              </div>
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancelar</button>
          <button type="submit" class="btn btn-primary">Salvar</button>
        </div>
      </form>
    </div>
  </div>
`;

const modalEl = document.getElementById("userModal");
const modal = new bootstrap.Modal(modalEl);

// ---- Cache de perfis + aliases disponiveis ---------------------------------
let allProfiles = [];   // [{id, code, label, ...}]
let allTableAliases = [];   // [{alias, label, profiles?}] — Hotfix v2.0

async function loadProfileCatalog() {
  try {
    allProfiles = await api("/api/profiles");
  } catch (e) { /* admin precisa estar logado — silencioso */ }
  // Hotfix v2.0 — carrega os aliases para o multi-select "Tabelas extras"
  try {
    const data = await api("/api/protheus/aliases");
    allTableAliases = data.aliases || [];
  } catch (e) { /* sem conexao Protheus — operador pode salvar mesmo sem lista */ }
}

function populateTablesMultiSelect(selectedAliases = []) {
  const sel = document.getElementById("uTables");
  if (!sel) return;
  const set = new Set((selectedAliases || []).map(a => String(a).toUpperCase()));
  if (!allTableAliases.length) {
    sel.innerHTML = `<option disabled>Lista indisponível — Protheus offline</option>`;
    return;
  }
  sel.innerHTML = allTableAliases.map(a => {
    const isSel = set.has(a.alias.toUpperCase());
    return `<option value="${a.alias}" ${isSel ? "selected" : ""}>${a.alias} — ${a.label}</option>`;
  }).join("");
}

function selectedTableAliases() {
  const sel = document.getElementById("uTables");
  if (!sel) return [];
  return Array.from(sel.selectedOptions || []).map(o => o.value);
}

function renderProfileCheckboxes(selectedCodes = []) {
  const sel = new Set(selectedCodes);
  const box = document.getElementById("uProfiles");
  if (!allProfiles.length) {
    box.innerHTML = `<div class="text-muted small">Nenhum perfil cadastrado. Crie em "Perfis & Módulos".</div>`;
    return;
  }
  box.innerHTML = allProfiles.map(p => `
    <div class="form-check">
      <input class="form-check-input prof-chk" type="checkbox" value="${p.code}" id="up_${p.code}"
             ${sel.has(p.code) ? "checked" : ""}>
      <label class="form-check-label small" for="up_${p.code}">
        <strong>${p.label}</strong>
        <span class="text-muted">· <code>${p.code}</code> · ${p.tables.length} tabela(s)</span>
      </label>
    </div>
  `).join("");
}

function selectedProfileCodes() {
  return [...document.querySelectorAll(".prof-chk:checked")].map(c => c.value);
}

async function reload() {
  try {
    const showInactive = document.getElementById("chkShowInactive")?.checked;
    const list = await api("/api/users" + (showInactive ? "?include_deleted=true" : ""));
    const rows = document.getElementById("rows");
    if (!list.length) { rows.innerHTML = `<tr><td colspan="11" class="p-3 text-muted">Nenhum usuário</td></tr>`; return; }
    rows.innerHTML = list.map((u, i) => {
      const isDeleted = !!u.deleted_at;
      const statusCell = isDeleted
        ? `<span class="badge bg-danger" title="Inativado em ${formatBR(u.deleted_at)}">Inativo</span>`
        : (u.is_active ? '<span class="badge bg-success">Ativo</span>'
                       : '<span class="badge bg-secondary">Inativo</span>');
      const uname = (u.username || "").replace(/"/g, "&quot;");
      const purgeBtn = `<button class="btn btn-sm btn-danger ms-1" data-purge='${u.id}' data-uname="${uname}">🗑 Excluir</button>`;
      const actionsCell = isDeleted
        ? `<button class="btn btn-sm btn-outline-success me-1" data-react='${u.id}'>↺ Reativar</button>${purgeBtn}`
        : `<button class="btn btn-sm btn-outline-primary me-1" data-edit='${u.id}'>Editar</button>
           <button class="btn btn-sm btn-outline-warning me-1" data-reset='${u.id}'>Reset senha</button>
           <button class="btn btn-sm btn-outline-secondary me-1" data-del='${u.id}'>Inativar</button>${purgeBtn}`;
      return `
      <tr class="${isDeleted ? 'text-muted' : ''}">
        <td class="text-muted">${i + 1}</td>
        <td><strong>${u.username}</strong></td>
        <td>${u.full_name || ""}</td>
        <td>${u.email}</td>
        <td><span class="badge bg-${u.role === "admin" ? "primary" : "secondary"}">${u.role}</span></td>
        <td><small>${(u.allowed_profiles || []).map(p => `<span class="badge bg-info text-dark me-1">${p}</span>`).join("") || `<span class="text-muted">sem perfil</span>`}</small></td>
        <td><small>${u.allowed_actions.join(", ") || "-"}</small></td>
        <td><small>${u.created_at ? formatBR(u.created_at) : "—"}</small></td>
        <td><small>${u.created_by || '<span class="text-muted">—</span>'}</small></td>
        <td>${statusCell}</td>
        <td class="text-end">${actionsCell}</td>
      </tr>`;
    }).join("");

    rows.querySelectorAll("[data-edit]").forEach(b =>
      b.addEventListener("click", () => openEdit(list.find(x => x.id == b.dataset.edit))));
    rows.querySelectorAll("[data-react]").forEach(b =>
      b.addEventListener("click", async () => {
        if (!confirm("Reativar este usuário? O acesso e as permissões anteriores voltam.")) return;
        try { await api(`/api/users/${b.dataset.react}/reactivate`, { method: "POST" }); reload(); toast("Usuário reativado", "success"); }
        catch (e) { toast(e.message, "danger"); }
      }));
    rows.querySelectorAll("[data-del]").forEach(b =>
      b.addEventListener("click", async () => {
        if (!confirm("Inativar este usuário? (reversível — pode reativar depois)")) return;
        try { await api(`/api/users/${b.dataset.del}`, { method: "DELETE" }); reload(); }
        catch (e) { toast(e.message, "danger"); }
      }));
    rows.querySelectorAll("[data-purge]").forEach(b =>
      b.addEventListener("click", async () => {
        const uname = b.dataset.uname || "";
        if (!confirm(
          `EXCLUIR DEFINITIVAMENTE o usuário "${uname}"?\n\n` +
          `Esta ação é PERMANENTE e remove acessos, sessões, agendamentos e ` +
          `consultas salvas deste usuário (o histórico de auditoria é preservado).\n\n` +
          `"Inativar" é reversível; "Excluir" não.`
        )) return;
        try { await api(`/api/users/${b.dataset.purge}/purge`, { method: "DELETE" }); reload(); toast("Usuário excluído", "success"); }
        catch (e) { toast(e.message, "danger"); }
      }));
    rows.querySelectorAll("[data-reset]").forEach(b =>
      b.addEventListener("click", async () => {
        if (!confirm("Gerar nova senha temporária?")) return;
        try {
          const r = await api(`/api/users/${b.dataset.reset}/reset-password`, { method: "POST" });
          alert(`Senha temporária: ${r.temp_password}\n(o usuário precisará trocá-la no próximo login)`);
        } catch (e) { toast(e.message, "danger"); }
      }));
  } catch (e) { toast(e.message, "danger"); }
}

function openCreate() {
  document.getElementById("userModalTitle").textContent = "Novo usuário";
  document.getElementById("pwdWrap").style.display = "";
  document.getElementById("pwdWrap2").style.display = "";
  document.getElementById("uId").value = "";
  // Hotfix v2.0 — uTables agora e' multi-select (nao limpa via .value="")
  ["uUsername","uEmail","uFullName","uPassword","uPassword2"].forEach(id => document.getElementById(id).value = "");
  document.getElementById("uRole").value = "operator";
  document.getElementById("uActive").value = "true";
  document.querySelectorAll(".act").forEach(c => c.checked = c.value === "view");
  document.getElementById("uUsername").disabled = false;
  renderProfileCheckboxes([]);
  populateTablesMultiSelect([]);
  modal.show();
}

function openEdit(u) {
  document.getElementById("userModalTitle").textContent = `Editar — ${u.username}`;
  document.getElementById("pwdWrap").style.display = "none";
  document.getElementById("pwdWrap2").style.display = "none";
  document.getElementById("uId").value = u.id;
  document.getElementById("uUsername").value = u.username;
  document.getElementById("uUsername").disabled = true;
  document.getElementById("uEmail").value = u.email;
  document.getElementById("uFullName").value = u.full_name || "";
  document.getElementById("uRole").value = u.role;
  document.getElementById("uActive").value = String(u.is_active);
  // Hotfix v2.0 — pre-seleciona aliases no multi-select
  populateTablesMultiSelect(u.allowed_tables || []);
  document.querySelectorAll(".act").forEach(c => c.checked = u.allowed_actions.includes(c.value));
  renderProfileCheckboxes(u.allowed_profiles || []);
  modal.show();
}

document.getElementById("btnNew").onclick = openCreate;
document.getElementById("chkShowInactive").addEventListener("change", reload);

document.getElementById("userForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = document.getElementById("uId").value;
  const actions = [...document.querySelectorAll(".act:checked")].map(c => c.value);
  const payload = {
    email: document.getElementById("uEmail").value,
    full_name: document.getElementById("uFullName").value || null,
    role: document.getElementById("uRole").value,
    is_active: document.getElementById("uActive").value === "true",
    // Hotfix v2.0 — le do multi-select (em vez de CSV de texto livre)
    allowed_tables: selectedTableAliases(),
    allowed_actions: actions,
    allowed_profiles: selectedProfileCodes(),
  };
  try {
    if (id) {
      await api(`/api/users/${id}`, { method: "PUT", body: payload });
    } else {
      const pwd  = document.getElementById("uPassword").value;
      const pwd2 = document.getElementById("uPassword2").value;
      if (!pwd || pwd.length < 8) return toast("Senha deve ter ao menos 8 caracteres", "warning");
      if (pwd !== pwd2)            return toast("As senhas não conferem", "warning");
      payload.username = document.getElementById("uUsername").value.trim();
      payload.password = pwd;
      await api("/api/users", { method: "POST", body: payload });
    }
    modal.hide();
    reload();
    toast("Usuário salvo", "success");
  } catch (err) { toast(err.message, "danger"); }
});

(async () => { await loadProfileCatalog(); await reload(); })();

/* Perfis & Módulos — CRUD de perfis + matriz alias x perfil (Fase 4.A). */
import { api, auth, toast, withSpinner } from "./api.js";
import { renderLayout } from "./layout.js";

if (!auth.isAdmin()) { location.href = "dashboard.html"; }

renderLayout({ active: "profiles", title: "Perfis & Módulos" });

const $ = (id) => document.getElementById(id);

document.getElementById("page").innerHTML = `
  <div class="d-flex justify-content-between align-items-center mb-3 flex-wrap gap-2">
    <span class="text-muted small">
      Agrupe as tabelas Protheus em <strong>perfis</strong> (Logística, Financeiro…)
      e vincule usuários a perfis. O Builder Visual de cada usuário mostra apenas
      as tabelas do(s) seu(s) perfil(s).
    </span>
    <button class="btn btn-primary" id="btnNew">+ Novo perfil</button>
  </div>

  <div class="row g-3">
    <!-- Lista de perfis -->
    <div class="col-lg-4">
      <div class="table-card">
        <div class="table-toolbar"><span class="small text-muted">Perfis cadastrados</span></div>
        <div class="list-group list-group-flush" id="profileList">
          <div class="p-3 text-muted small">Carregando…</div>
        </div>
      </div>
    </div>

    <!-- Matriz: tabelas do perfil selecionado -->
    <div class="col-lg-8">
      <div class="table-card p-3" id="matrixCard">
        <div class="text-muted small">Selecione um perfil à esquerda para editar.</div>
      </div>
    </div>
  </div>

  <!-- Modal criar/editar perfil -->
  <div class="modal fade" id="profileModal" tabindex="-1">
    <div class="modal-dialog">
      <form class="modal-content" id="profileForm">
        <div class="modal-header">
          <h5 class="modal-title" id="profileModalTitle">Novo perfil</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
        </div>
        <div class="modal-body">
          <input type="hidden" id="pId">
          <div class="mb-3">
            <label class="form-label">Code (UPPER, sem espaços)</label>
            <input id="pCode" class="form-control" required pattern="^[A-Z][A-Z0-9_]*$"
                   placeholder="LOGISTICA, COMPRAS_ESPECIAIS…">
            <div class="form-text">Identificador interno. Não pode mudar depois.</div>
          </div>
          <div class="mb-3">
            <label class="form-label">Nome amigável</label>
            <input id="pLabel" class="form-control" required placeholder="Logística">
          </div>
          <div class="mb-3">
            <label class="form-label">Descrição (opcional)</label>
            <textarea id="pDescription" class="form-control" rows="2"></textarea>
          </div>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-outline-danger me-auto" id="btnDeleteProfile" hidden>Excluir</button>
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancelar</button>
          <button type="submit" class="btn btn-primary" id="pSave">
            <span class="spinner-border spinner-border-sm me-2 d-none" role="status"></span>
            <span class="label">Salvar</span>
          </button>
        </div>
      </form>
    </div>
  </div>
`;

const profileModal = new bootstrap.Modal("#profileModal");

let profiles = [];
let selectedId = null;
let allAliases = [];   // do catalogo Protheus

async function loadAll() {
  try {
    profiles = await api("/api/profiles");
    renderProfileList();
    // Aliases do catalogo (para a matriz)
    const a = await api("/api/protheus/aliases");
    allAliases = a.aliases;
  } catch (e) { toast(e.message, "danger"); }
}

function renderProfileList() {
  const list = $("profileList");
  if (!profiles.length) {
    list.innerHTML = `<div class="p-3 text-muted small">Nenhum perfil cadastrado</div>`;
    return;
  }
  list.innerHTML = profiles.map(p => `
    <a href="#" class="list-group-item list-group-item-action d-flex justify-content-between align-items-start
        ${p.id === selectedId ? 'active' : ''}" data-pid="${p.id}">
      <div>
        <strong>${p.label}</strong>
        <div class="small ${p.id === selectedId ? '' : 'text-muted'}">
          <code class="${p.id === selectedId ? 'text-white' : ''}">${p.code}</code> ·
          ${p.tables.length} tabela(s) · ${p.user_count} usuário(s)
        </div>
      </div>
      <button class="btn btn-sm btn-outline-${p.id === selectedId ? 'light' : 'secondary'}"
              data-edit="${p.id}" title="Editar">✏️</button>
    </a>
  `).join("");

  list.querySelectorAll("[data-pid]").forEach(a => {
    a.addEventListener("click", e => {
      if (e.target.closest("[data-edit]")) return;
      e.preventDefault();
      selectedId = parseInt(a.dataset.pid, 10);
      renderProfileList();
      renderMatrix();
    });
  });
  list.querySelectorAll("[data-edit]").forEach(b => {
    b.addEventListener("click", e => {
      e.preventDefault(); e.stopPropagation();
      const p = profiles.find(x => x.id == b.dataset.edit);
      openEdit(p);
    });
  });
}

function renderMatrix() {
  const card = $("matrixCard");
  if (!selectedId) {
    card.innerHTML = `<div class="text-muted small">Selecione um perfil à esquerda para editar.</div>`;
    return;
  }
  const p = profiles.find(x => x.id === selectedId);
  if (!p) return;

  const inProfile = new Set(p.tables);
  card.innerHTML = `
    <div class="d-flex justify-content-between align-items-center mb-3">
      <div>
        <h5 class="mb-0">${p.label}</h5>
        <small class="text-muted"><code>${p.code}</code> — ${p.description || "sem descrição"}</small>
      </div>
      <span class="badge bg-secondary">${p.tables.length} tabela(s)</span>
    </div>

    <div class="mb-2 d-flex justify-content-between align-items-center">
      <strong class="small text-uppercase text-muted">Tabelas do perfil</strong>
      <input id="filterAlias" class="form-control form-control-sm" placeholder="🔎 filtrar alias ou descrição" style="max-width:280px">
    </div>

    <div class="col-grid" id="aliasGrid" style="max-height:60vh">
      ${allAliases.map(a => {
        const checked = inProfile.has(a.alias);
        return `
          <div class="form-check" data-alias="${a.alias}" data-search="${a.alias.toLowerCase()} ${a.label.toLowerCase()}">
            <input class="form-check-input alias-chk" type="checkbox" id="al_${a.alias}"
                   value="${a.alias}" ${checked ? "checked" : ""}>
            <label class="form-check-label small" for="al_${a.alias}">
              <strong>${a.alias}</strong> — ${a.label}
              ${a.profiles?.length ? `<span class="text-muted"> · ${a.profiles.join(", ")}</span>` : ""}
            </label>
          </div>`;
      }).join("")}
    </div>
  `;

  // Filtro de busca
  $("filterAlias").addEventListener("input", e => {
    const q = e.target.value.trim().toLowerCase();
    card.querySelectorAll("[data-search]").forEach(el => {
      el.style.display = !q || el.dataset.search.includes(q) ? "" : "none";
    });
  });

  // Toggle alias
  card.querySelectorAll(".alias-chk").forEach(chk => {
    chk.addEventListener("change", async e => {
      const alias = e.target.value;
      const wasChecked = e.target.checked;
      try {
        if (wasChecked) {
          await api(`/api/profiles/${selectedId}/tables`, {
            method: "POST", body: { alias },
          });
        } else {
          await api(`/api/profiles/${selectedId}/tables/${alias}`, { method: "DELETE" });
        }
        await refreshSelected();
        toast(wasChecked ? `Adicionado ${alias}` : `Removido ${alias}`, "success");
      } catch (err) {
        toast(err.message, "danger");
        e.target.checked = !wasChecked;  // rollback visual
      }
    });
  });
}

async function refreshSelected() {
  try {
    profiles = await api("/api/profiles");
    renderProfileList();
    const p = profiles.find(x => x.id === selectedId);
    if (p) {
      // Atualiza apenas o contador no card sem perder o filtro
      const badge = $("matrixCard").querySelector(".badge");
      if (badge) badge.textContent = `${p.tables.length} tabela(s)`;
    }
  } catch {}
}

// ---- Modal criar/editar -----------------------------------------------------
function openCreate() {
  $("profileModalTitle").textContent = "Novo perfil";
  $("pId").value = "";
  $("pCode").value = "";
  $("pCode").disabled = false;
  $("pLabel").value = "";
  $("pDescription").value = "";
  $("btnDeleteProfile").hidden = true;
  profileModal.show();
}

function openEdit(p) {
  $("profileModalTitle").textContent = `Editar — ${p.code}`;
  $("pId").value = p.id;
  $("pCode").value = p.code;
  $("pCode").disabled = true;
  $("pLabel").value = p.label;
  $("pDescription").value = p.description || "";
  $("btnDeleteProfile").hidden = false;
  profileModal.show();
}

$("btnNew").onclick = openCreate;

$("profileForm").addEventListener("submit", async e => {
  e.preventDefault();
  await withSpinner($("pSave"), async () => {
    const id = $("pId").value;
    try {
      if (id) {
        await api(`/api/profiles/${id}`, { method: "PUT", body: {
          label: $("pLabel").value, description: $("pDescription").value || null,
        }});
        toast("Perfil atualizado", "success");
      } else {
        const created = await api("/api/profiles", { method: "POST", body: {
          code: $("pCode").value.trim().toUpperCase(),
          label: $("pLabel").value.trim(),
          description: $("pDescription").value || null,
        }});
        selectedId = created.id;
        toast("Perfil criado", "success");
      }
      profileModal.hide();
      await loadAll();
      renderMatrix();
    } catch (err) { toast(err.message, "danger"); }
  }, "Salvando…");
});

$("btnDeleteProfile").addEventListener("click", async () => {
  const id = $("pId").value;
  if (!id) return;
  const p = profiles.find(x => x.id == id);
  if (!confirm(`Excluir o perfil "${p?.label}"? Todos os vínculos (tabelas e usuários) serão removidos.`)) return;
  try {
    await api(`/api/profiles/${id}`, { method: "DELETE" });
    toast("Perfil removido", "success");
    profileModal.hide();
    if (selectedId == id) selectedId = null;
    await loadAll();
    renderMatrix();
  } catch (err) { toast(err.message, "danger"); }
});

loadAll();

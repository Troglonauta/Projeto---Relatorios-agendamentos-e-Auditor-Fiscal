/* Administracao: 4 abas (Status, Configuracoes, Catalogo de Erros, Manutencao). */
import { api, auth, toast, withSpinner } from "./api.js";
import { renderLayout } from "./layout.js";

if (!auth.isAdmin()) { location.href = "dashboard.html"; }

renderLayout({ active: "admin", title: "Administração" });

const $ = (id) => document.getElementById(id);

document.getElementById("page").innerHTML = `
  <ul class="nav nav-tabs mb-3" id="adminTabs">
    <li class="nav-item"><a class="nav-link active" data-bs-toggle="tab" href="#tabStatus">📊 Status</a></li>
    <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabConfig">⚙️ Configurações</a></li>
    <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabCatalog">📖 Catálogo de Erros</a></li>
    <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabMaint">🛠️ Manutenção</a></li>
    <li class="nav-item"><a class="nav-link" data-bs-toggle="tab" href="#tabSessions">👥 Sessões Ativas</a></li>
  </ul>

  <div class="tab-content">

    <!-- ===== Aba Status ===== -->
    <div class="tab-pane fade show active" id="tabStatus">
      <div class="d-flex justify-content-between mb-3">
        <span class="text-muted small">Snapshot da saúde dos componentes. Atualize quando quiser.</span>
        <button class="btn btn-outline-primary btn-sm" id="btnRefresh">
          <span class="spinner-border spinner-border-sm me-2 d-none" role="status"></span>
          <span class="label">Atualizar</span>
        </button>
      </div>
      <div class="row g-3">
        <div class="col-md-6"><div class="table-card p-3 h-100" id="cardProtheus"></div></div>
        <div class="col-md-6"><div class="table-card p-3 h-100" id="cardBroker"></div></div>
        <div class="col-md-6"><div class="table-card p-3 h-100" id="cardSched"></div></div>
        <div class="col-md-6"><div class="table-card p-3 h-100" id="cardJobs"></div></div>
      </div>
      <div class="mt-3 small text-muted" id="meta">—</div>
    </div>

    <!-- ===== Aba Configurações ===== -->
    <div class="tab-pane fade" id="tabConfig">
      <ul class="nav nav-pills mb-3">
        <li class="nav-item"><a class="nav-link active" data-bs-toggle="pill" href="#cfgBranding">Identidade</a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="pill" href="#cfgOperation">Operação</a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="pill" href="#cfgDb">Banco Protheus</a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="pill" href="#cfgSmtp">E-mail (SMTP)</a></li>
        <li class="nav-item"><a class="nav-link" data-bs-toggle="pill" href="#cfgFiscal">Auditor Fiscal</a></li>
      </ul>

      <div class="tab-content">
        <!-- Branding -->
        <div class="tab-pane fade show active" id="cfgBranding">
          <div class="table-card p-3">
            <h6 class="text-uppercase text-muted small mb-3">Identidade visual</h6>
            <div class="row g-2">
              <div class="col-md-6"><label class="form-label">Nome do sistema</label>
                <input id="bAppName" class="form-control"></div>
              <div class="col-md-3"><label class="form-label">Cor primária</label>
                <input id="bColor" type="color" class="form-control form-control-color"></div>
              <div class="col-md-12"><label class="form-label">Logo (substitui o atual)</label>
                <input id="bLogo" type="file" accept=".png,.jpg,.jpeg,.svg" class="form-control"></div>
            </div>
            <button class="btn btn-primary mt-3" id="btnSaveBranding">
              <span class="spinner-border spinner-border-sm me-2 d-none" role="status"></span>
              <span class="label">Salvar identidade</span>
            </button>
          </div>
        </div>

        <!-- Operação -->
        <div class="tab-pane fade" id="cfgOperation">
          <div class="table-card p-3">
            <h6 class="text-uppercase text-muted small mb-3">Filiais da operação</h6>
            <p class="small text-muted mb-2">
              Lista de filiais que aparece nos seletores do Builder, Agendamentos e Auditor Fiscal.
              Use 2 dígitos por filial (ex: <code>01</code>) separados por vírgula.
              Quando uma filial nova entrar em operação, adicione aqui — não dependa do que existe
              nas tabelas (algumas só ganham registro depois do primeiro lançamento).
            </p>
            <div class="mb-3">
              <label class="form-label">Filiais (CSV)</label>
              <input id="opBranches" class="form-control" placeholder="01,02,03,04,05,06,07,08">
              <div class="form-text">Default: 01,02,03,04,05,06,07,08</div>
            </div>
            <button class="btn btn-primary" id="btnSaveOperation">
              <span class="spinner-border spinner-border-sm me-2 d-none" role="status"></span>
              <span class="label">Salvar filiais</span>
            </button>
          </div>
        </div>

        <!-- DB -->
        <div class="tab-pane fade" id="cfgDb">
          <div class="table-card p-3">
            <h6 class="text-uppercase text-muted small mb-3">Conexão SQL Server (Protheus)</h6>
            <div class="alert alert-warning small">⚠️ Alterar a URL exige <strong>Recarregar</strong> ao final.
              A senha atual é mantida — só preencha se quiser trocar.</div>
            <div class="mb-3"><label class="form-label">URL atual</label>
              <input id="dbUrlCurrent" class="form-control" readonly></div>
            <div class="mb-3"><label class="form-label">Nova URL (cole completa, com senha URL-encoded)</label>
              <input id="dbUrl" class="form-control"
                placeholder="mssql+pyodbc://user:pass@host:1433/db?driver=ODBC+Driver+17+for+SQL+Server"></div>
            <div class="row g-2">
              <div class="col-md-6"><label class="form-label">Pool size</label>
                <input id="dbPoolSize" type="number" class="form-control" min="1" max="100"></div>
              <div class="col-md-6"><label class="form-label">Max overflow</label>
                <input id="dbMaxOver" type="number" class="form-control" min="0" max="200"></div>
            </div>
            <div class="d-flex gap-2 mt-3">
              <button class="btn btn-outline-primary" id="btnTestDb">
                <span class="spinner-border spinner-border-sm me-2 d-none" role="status"></span>
                <span class="label">Testar nova URL</span>
              </button>
              <button class="btn btn-primary" id="btnSaveDb">
                <span class="spinner-border spinner-border-sm me-2 d-none" role="status"></span>
                <span class="label">Salvar e aplicar</span>
              </button>
            </div>
          </div>
        </div>

        <!-- SMTP -->
        <div class="tab-pane fade" id="cfgSmtp">
          <div class="table-card p-3">
            <h6 class="text-uppercase text-muted small mb-3">Servidor de e-mail (SMTP)</h6>
            <div class="row g-2">
              <div class="col-md-8"><label class="form-label">Servidor</label>
                <input id="sHost" class="form-control"></div>
              <div class="col-md-4"><label class="form-label">Porta</label>
                <input id="sPort" type="number" class="form-control"></div>
              <div class="col-md-6"><label class="form-label">Usuário</label>
                <input id="sUser" class="form-control"></div>
              <div class="col-md-6"><label class="form-label">Senha
                <small class="text-muted">(deixe vazio para manter atual)</small></label>
                <input id="sPwd" type="password" class="form-control"></div>
              <div class="col-md-8"><label class="form-label">Remetente</label>
                <input id="sFrom" type="email" class="form-control"></div>
              <div class="col-md-4"><label class="form-label">STARTTLS</label>
                <select id="sTls" class="form-select"><option value="true">Sim</option><option value="false">Não</option></select></div>
            </div>
            <div class="d-flex gap-2 mt-3">
              <button class="btn btn-outline-primary" id="btnTestSmtp">
                <span class="spinner-border spinner-border-sm me-2 d-none" role="status"></span>
                <span class="label">Enviar e-mail de teste</span>
              </button>
              <button class="btn btn-primary" id="btnSaveSmtp">
                <span class="spinner-border spinner-border-sm me-2 d-none" role="status"></span>
                <span class="label">Salvar SMTP</span>
              </button>
            </div>
          </div>
        </div>

        <!-- Fiscal -->
        <div class="tab-pane fade" id="cfgFiscal">
          <div class="table-card p-3">
            <h6 class="text-uppercase text-muted small mb-3">Auditor Fiscal</h6>
            <div class="alert alert-success small">
              🔍 <strong>Motor:</strong> Auditoria Interna (SDS/SDT × SF1/SD1).
              Compara o XML internalizado pelo ERP contra a nota classificada, sem
              chamadas externas. Tolerâncias configuráveis abaixo.
            </div>
            <div class="row g-2">
              <div class="col-md-12">
                <div class="form-check form-switch">
                  <input class="form-check-input" type="checkbox" id="fNotifyEnabled" checked>
                  <label class="form-check-label" for="fNotifyEnabled">
                    ✉️ Enviar e-mails do Auditor Fiscal
                    <small class="text-muted">— desligue para não receber nenhum e-mail (manual ou agendado)</small>
                  </label>
                </div>
              </div>
              <div class="col-md-12"><label class="form-label">E-mail para notificações (vírgula)</label>
                <input id="fNotify" class="form-control" placeholder="fiscal@fertimaxi.com.br"></div>
              <div class="col-md-12"><label class="form-label">Filiais para auditoria automática (vírgula)</label>
                <input id="fAutoBranches" class="form-control" placeholder="01,02"></div>

              <!-- Sprint 11 — Cron Auditor Fiscal autônomo -->
              <div class="col-md-12 mt-3 pt-3 border-top">
                <label class="form-label">
                  🤖 Auditoria Automática <small class="text-muted">(cron)</small>
                </label>
                <div class="row g-2">
                  <div class="col-md-6">
                    <select id="fSchedule" class="form-select">
                      <option value="">— carregando opções —</option>
                    </select>
                    <div class="form-text" id="fScheduleHint">
                      Quando ativada, roda o Auditor Fiscal nas filiais marcadas em
                      <em>"Filiais para auditoria automática"</em>. E-mail de
                      consolidação é enviado mesmo se 0 anomalias forem encontradas.
                    </div>
                  </div>
                  <div class="col-md-6 d-flex align-items-end gap-2">
                    <button class="btn btn-outline-primary" id="btnSaveSchedule" type="button">
                      <span class="spinner-border spinner-border-sm me-2 d-none" role="status"></span>
                      <span class="label">Salvar frequência</span>
                    </button>
                    <span class="text-muted small" id="fScheduleNext">próxima: —</span>
                  </div>
                </div>
              </div>
            </div>
            <div class="d-flex gap-2 mt-3">
              <button class="btn btn-outline-secondary" id="btnPreviewEmail">
                📧 Preview e-mail de anomalias
              </button>
              <button class="btn btn-primary ms-auto" id="btnSaveFiscal">
                <span class="spinner-border spinner-border-sm me-2 d-none" role="status"></span>
                <span class="label">Salvar opções do Auditor</span>
              </button>
            </div>
            <div class="form-text mt-2">
              "Preview e-mail" renderiza o template HTML com dados mock (inclui
              divergências de NCM para você visualizar o bloco vermelho de
              compliance no topo).
            </div>

            <!-- Sprint 8 Part 3 — Webhook de alerta (Teams/Slack/genérico) -->
            <hr class="my-4">
            <h6 class="text-uppercase text-muted small mb-2">🚨 Webhook de Alertas</h6>
            <p class="small text-muted mb-2">
              URL chamada por <strong>POST JSON</strong> ao final de cada auditoria
              quando houver pelo menos 1 divergência <strong>crítica</strong>.
              Compatível com Slack, Microsoft Teams e webhooks genéricos
              (payload <code>{text, severity, stats}</code>).
              A URL é armazenada criptografada com Fernet.
            </p>
            <div class="row g-2">
              <div class="col-md-12">
                <label class="form-label">URL do Webhook
                  <small class="text-muted">(vazio = desativa alertas)</small>
                </label>
                <input id="fWebhook" class="form-control"
                       placeholder="https://hooks.slack.com/... ou https://outlook.office.com/webhook/...">
                <div class="form-text" id="fWebhookHint">—</div>
              </div>
            </div>
            <div class="d-flex gap-2 mt-3">
              <button class="btn btn-outline-info" id="btnTestWebhook" type="button">
                <span class="spinner-border spinner-border-sm me-2 d-none" role="status"></span>
                <span class="label">🧪 Enviar mensagem de teste</span>
              </button>
              <button class="btn btn-primary ms-auto" id="btnSaveWebhook" type="button">
                <span class="spinner-border spinner-border-sm me-2 d-none" role="status"></span>
                <span class="label">Salvar Webhook</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ===== Aba Catálogo de Erros ===== -->
    <div class="tab-pane fade" id="tabCatalog">
      <div class="table-card p-3">
        <div class="d-flex justify-content-between mb-2">
          <h6 class="text-uppercase text-muted small m-0">Catálogo de códigos de erro</h6>
          <span class="text-muted small">Use Ctrl+F para buscar um código (ex: ERR-DB-001)</span>
        </div>
        <div id="catalogBox" class="mt-3" style="max-height:70vh;overflow:auto"></div>
      </div>
    </div>

    <!-- ===== Aba Manutenção ===== -->
    <div class="tab-pane fade" id="tabMaint">
      <div class="table-card p-3 mb-3">
        <h6 class="text-uppercase text-muted small mb-3">Hot-reload</h6>
        <p class="small text-muted mb-2">
          Aplica novas configurações sem derrubar o servidor.
          Itens que exigem restart real aparecem em <code>requires_restart</code>.
        </p>
        <button class="btn btn-warning" id="btnReload">
          <span class="spinner-border spinner-border-sm me-2 d-none" role="status"></span>
          <span class="label">Recarregar configurações</span>
        </button>
      </div>

      <div class="table-card p-3">
        <h6 class="text-uppercase text-muted small mb-3">Reiniciar processo</h6>
        <p class="small text-muted mb-2">
          Necessário ao trocar <code>JWT_SECRET</code>, <code>APP_PORT</code> ou
          <code>MASTER_KEY</code>. A UI fica indisponível por ~5s; o supervisor
          (systemd no LXC / <code>scripts/supervisor.py</code> no dev) re-spawn automaticamente.
        </p>
        <button class="btn btn-danger" id="btnRestart">Reiniciar processo</button>
      </div>

      <!-- Sprint 6 — Dicionario SX3 -->
      <div class="table-card p-3 mt-3">
        <h6 class="text-uppercase text-muted small mb-3">📘 Dicionário SX3 (humanização de headers)</h6>
        <p class="small text-muted mb-2">
          Cache em memória dos títulos amigáveis dos campos Protheus
          (<code>X3_TITULO</code>). Quando o cliente customiza títulos no
          Configurador, recarregue aqui para refletir nos Excels.
        </p>
        <div class="d-flex gap-2 align-items-center flex-wrap">
          <button class="btn btn-outline-primary" id="btnSx3Reload">
            <span class="spinner-border spinner-border-sm me-2 d-none" role="status"></span>
            <span class="label">Recarregar SX3</span>
          </button>
          <span class="small text-muted" id="sx3Stats">—</span>
        </div>
      </div>
    </div>

    <!-- ===== Aba Sessões Ativas (Sprint 6) ===== -->
    <div class="tab-pane fade" id="tabSessions">
      <div class="table-card p-3">
        <div class="d-flex justify-content-between mb-3 flex-wrap gap-2">
          <div>
            <h6 class="text-uppercase text-muted small m-0">Sessões JWT ativas</h6>
            <p class="small text-muted m-0 mt-1">
              Contas com login simultâneo. O limite atual é configurável via
              <code>MAX_CONCURRENT_SESSIONS</code> (default 3). Use "Derrubar"
              para revogar tokens esquecidos em outra máquina.
            </p>
          </div>
          <div class="d-flex gap-2">
            <div class="form-check align-self-center">
              <input class="form-check-input" type="checkbox" id="sessIncludeRevoked">
              <label class="form-check-label small" for="sessIncludeRevoked">Incluir revogadas</label>
            </div>
            <button class="btn btn-outline-primary btn-sm" id="btnReloadSessions">
              <span class="spinner-border spinner-border-sm me-2 d-none" role="status"></span>
              <span class="label">🔄 Atualizar</span>
            </button>
          </div>
        </div>
        <div id="sessionsBox" class="table-responsive">
          <div class="text-muted small p-3">Carregando…</div>
        </div>
      </div>
    </div>
  </div>

  <!-- Modal: Preview do e-mail de anomalias (Sprint 4.B Frontend) -->
  <div class="modal fade email-preview-modal" id="emailPreviewModal" tabindex="-1">
    <div class="modal-dialog modal-dialog-centered">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title">📧 Preview — E-mail de anomalias fiscais</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
        </div>
        <div class="modal-body">
          <p class="small text-muted mb-3">
            Renderização do template HTML com <strong>dados MOCK</strong> incluindo
            divergências de NCM, valor e CFOP. Use para validar a aparência do
            e-mail antes de uma execução real do Auditor.
          </p>
          <iframe id="emailPreviewFrame" sandbox="allow-same-origin"></iframe>
        </div>
        <div class="modal-footer">
          <button class="btn btn-secondary" data-bs-dismiss="modal">Fechar</button>
        </div>
      </div>
    </div>
  </div>

  <!-- Modal: Restart -->
  <div class="modal fade" id="restartModal" tabindex="-1">
    <div class="modal-dialog">
      <div class="modal-content">
        <div class="modal-header"><h5 class="modal-title">Confirmar restart</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
        <div class="modal-body">
          <p>O processo será encerrado e re-spawnado pelo supervisor.</p>
          <p>A interface ficará indisponível por <strong>~5 segundos</strong>.</p>
        </div>
        <div class="modal-footer">
          <button class="btn btn-secondary" data-bs-dismiss="modal">Cancelar</button>
          <button class="btn btn-danger" id="confirmRestart">Reiniciar agora</button>
        </div>
      </div>
    </div>
  </div>
`;

// ============================================================
//  Aba Status — health detail
// ============================================================
function ok(b) { return b ? '<span class="badge bg-success">OK</span>' : '<span class="badge bg-danger">FALHA</span>'; }

function renderStatus(detail) {
  const c = detail.components || {};

  // Protheus
  const pt = c.protheus || {};
  $("cardProtheus").innerHTML = `
    <h6 class="text-uppercase text-muted small">📦 Banco Protheus</h6>
    <div class="mt-2">${ok(pt.ok)}</div>
    ${pt.ok ? `
      <div class="small mt-3">
        <div>📊 Pool atual: <strong>${pt.pool_size ?? "?"}</strong> conexões</div>
        <div>🔒 Em uso agora: <strong>${pt.checked_out ?? 0}</strong></div>
        <div>⏫ Overflow: <strong>${pt.overflow ?? 0}</strong></div>
      </div>` : `<div class="text-danger small mt-2">${pt.error || ""}</div>`}
  `;

  // Broker (Redis ou SQLite)
  const br = c.broker || {};
  const brIcon = br.type === "sqlite" ? "💾" : "🔴";
  const brLabel = br.type === "sqlite" ? "SQLite (modo dev)" : "Redis";
  $("cardBroker").innerHTML = `
    <h6 class="text-uppercase text-muted small">${brIcon} Broker da fila</h6>
    <div class="mt-2">${ok(br.ok)} <span class="badge bg-secondary ms-1">${brLabel}</span></div>
    ${br.url ? `<div class="small mt-2 text-break"><code>${br.url}</code></div>` : ""}
    ${br.note ? `<div class="small text-muted mt-2">ℹ️ ${br.note}</div>` : ""}
    ${br.error ? `<div class="small text-danger mt-2">${br.error}</div>` : ""}
  `;

  // Scheduler
  const sc = c.scheduler || {};
  $("cardSched").innerHTML = `
    <h6 class="text-uppercase text-muted small">⏰ Scheduler (cron)</h6>
    <div class="mt-2">${ok(sc.ok)}</div>
    ${sc.jobs ? `<ul class="small mt-2 mb-0">${sc.jobs.map(j => `
      <li><strong>${j.id}</strong> · próx: <code>${j.next}</code></li>`).join("")}</ul>` : ""}
  `;

  // Jobs
  const jb = c.jobs || {};
  if (jb.ok) {
    const by = jb.by_status || {};
    const total = Object.values(by).reduce((s, v) => s + v, 0);
    $("cardJobs").innerHTML = `
      <h6 class="text-uppercase text-muted small">🛠 Jobs no banco</h6>
      <div class="mt-2">${ok(true)} · <span class="text-muted">${total} no total</span></div>
      <div class="mt-3 small">
        ${["queued","running","done","failed","canceled"].map(s => `
          <div class="d-flex justify-content-between border-bottom py-1">
            <span class="text-muted">${s}</span>
            <strong>${by[s] || 0}</strong>
          </div>`).join("")}
      </div>`;
  } else {
    $("cardJobs").innerHTML = `<h6 class="text-uppercase text-muted small">🛠 Jobs</h6>
      <div class="mt-2">${ok(false)}</div>
      <div class="small text-danger mt-2">${jb.error || ""}</div>`;
  }

  $("meta").textContent = `📸 Snapshot em ${new Date(detail.timestamp).toLocaleString("pt-BR")} — ${detail.ok ? "Tudo OK" : "Algum componente com falha"}`;
}

async function refreshStatus() {
  await withSpinner($("btnRefresh"), async () => {
    try { renderStatus(await api("/api/admin/health/detail")); }
    catch (e) { toast(e.message, "danger"); }
  }, "Atualizando…");
}

$("btnRefresh").addEventListener("click", refreshStatus);

// ============================================================
//  Aba Configurações — carregar e editar
// ============================================================
async function loadConfig() {
  try {
    const c = await api("/api/admin/config");
    $("bAppName").value     = c.branding.app_name;
    $("bColor").value       = c.branding.primary_color;
    $("opBranches").value   = c.operation?.branches || "01,02,03,04,05,06,07,08";
    $("dbUrlCurrent").value = c.db.url_masked;
    $("dbPoolSize").value   = c.db.pool_size;
    $("dbMaxOver").value    = c.db.max_overflow;
    $("sHost").value        = c.smtp.host;
    $("sPort").value        = c.smtp.port;
    $("sUser").value        = c.smtp.user;
    $("sFrom").value        = c.smtp.sender;
    $("sTls").value         = c.smtp.use_tls ? "true" : "false";
    $("fNotify").value      = c.fiscal.notify_email || "";
    if ($("fNotifyEnabled")) $("fNotifyEnabled").checked = (c.fiscal.notify_enabled !== false);
    $("fAutoBranches").value= c.fiscal.auto_branches || "";
    // Sprint 8 Part 3 — Webhook (URL eh secret, backend so devolve preview/flag)
    const hint = $("fWebhookHint");
    if (c.fiscal.webhook_configured) {
      if (hint) {
        hint.innerHTML = `✓ Webhook configurado: <code>${c.fiscal.webhook_url_preview || "***"}</code>. ` +
                         `Para alterar, cole uma nova URL e salve. Para remover, deixe vazio e salve.`;
      }
    } else {
      if (hint) hint.textContent = "⚠️ Nenhum webhook configurado — alertas críticos NÃO serão enviados.";
    }
  } catch (e) { toast("Falha ao carregar config: " + e.message, "danger"); }
}

$("btnSaveOperation").addEventListener("click", async () => {
  await withSpinner($("btnSaveOperation"), async () => {
    try {
      const r = await api("/api/admin/config/operation", { method: "POST", body: {
        branches: $("opBranches").value,
      }});
      toast(`Filiais salvas: ${r.branches.join(", ")}`, "success");
      // Invalida cache de settings publicos para refletir imediatamente no Builder
      localStorage.removeItem("pr_settings");
    } catch (e) { toast(e.message, "danger"); }
  }, "Salvando…");
});

$("btnSaveBranding").addEventListener("click", async () => {
  await withSpinner($("btnSaveBranding"), async () => {
    try {
      await api("/api/admin/config/branding", { method: "POST", body: {
        app_name: $("bAppName").value, primary_color: $("bColor").value,
      }});
      // Logo (se selecionado)
      const file = $("bLogo").files[0];
      if (file) {
        const fd = new FormData(); fd.append("logo", file);
        await api("/api/admin/config/branding/logo", { method: "POST", form: fd });
      }
      toast("Identidade salva. Recarregue a página para ver as mudanças.", "success");
    } catch (e) { toast(e.message, "danger"); }
  }, "Salvando…");
});

$("btnTestDb").addEventListener("click", async () => {
  await withSpinner($("btnTestDb"), async () => {
    try {
      const r = await api("/api/admin/test/db", { method: "POST", body: {
        db_url: $("dbUrl").value, pool_size: 1, max_overflow: 0,
      }});
      r.ok ? toast("Conexão OK: " + r.detail, "success") : toast("Falha: " + r.detail, "danger");
    } catch (e) { toast(e.message, "danger"); }
  }, "Testando…");
});

$("btnSaveDb").addEventListener("click", async () => {
  if (!$("dbUrl").value.trim()) return toast("Cole a nova URL de conexão", "warning");
  await withSpinner($("btnSaveDb"), async () => {
    try {
      await api("/api/admin/config/db", { method: "POST", body: {
        db_url: $("dbUrl").value,
        pool_size: parseInt($("dbPoolSize").value, 10),
        max_overflow: parseInt($("dbMaxOver").value, 10),
      }});
      toast("Banco salvo e engine reiniciada", "success");
      $("dbUrl").value = "";
      loadConfig();
    } catch (e) { toast(e.message, "danger"); }
  }, "Salvando…");
});

$("btnTestSmtp").addEventListener("click", async () => {
  if (!$("sPwd").value) return toast("Para testar, informe a senha SMTP", "warning");
  await withSpinner($("btnTestSmtp"), async () => {
    try {
      const r = await api("/api/admin/test/smtp", { method: "POST", body: {
        host: $("sHost").value, port: parseInt($("sPort").value, 10),
        user: $("sUser").value, password: $("sPwd").value,
        sender: $("sFrom").value || $("sUser").value,
        use_tls: $("sTls").value === "true",
      }});
      r.ok ? toast("E-mail teste enviado: " + r.detail, "success") : toast("Falha: " + r.detail, "danger");
    } catch (e) { toast(e.message, "danger"); }
  }, "Testando…");
});

$("btnSaveSmtp").addEventListener("click", async () => {
  await withSpinner($("btnSaveSmtp"), async () => {
    try {
      const payload = {
        host: $("sHost").value,
        port: parseInt($("sPort").value, 10),
        user: $("sUser").value,
        sender: $("sFrom").value || $("sUser").value,
        use_tls: $("sTls").value === "true",
      };
      // Senha vazia = backend mantem a atual
      if ($("sPwd").value) payload.password = $("sPwd").value;
      const r = await api("/api/admin/config/smtp", { method: "POST", body: payload });
      toast(r.password_changed ? "SMTP salvo (senha trocada)" : "SMTP salvo (senha mantida)", "success");
      $("sPwd").value = "";
    } catch (e) { toast(e.message, "danger"); }
  }, "Salvando…");
});

$("btnPreviewEmail").addEventListener("click", async () => {
  /* Carrega o HTML mock do endpoint admin e renderiza num iframe modal.
     Usamos srcdoc em vez de src para nao precisar passar o JWT em cookies. */
  const modal = new bootstrap.Modal("#emailPreviewModal");
  const frame = $("emailPreviewFrame");
  frame.srcdoc = "<p style='font-family:sans-serif;padding:20px'>Gerando preview…</p>";
  modal.show();
  try {
    const res = await fetch("/api/admin/email/preview/fiscal", {
      headers: { "Authorization": `Bearer ${auth.token}` },
    });
    if (!res.ok) throw new Error(res.statusText);
    frame.srcdoc = await res.text();
  } catch (e) {
    frame.srcdoc = `<p style="color:red;font-family:sans-serif;padding:20px">Falha: ${e.message}</p>`;
  }
});

$("btnSaveFiscal").addEventListener("click", async () => {
  await withSpinner($("btnSaveFiscal"), async () => {
    try {
      await api("/api/admin/config/fiscal", { method: "POST", body: {
        FISCAL_NOTIFY_EMAIL: $("fNotify").value,
        FISCAL_NOTIFY_ENABLED: $("fNotifyEnabled") ? $("fNotifyEnabled").checked : true,
        FISCAL_AUTO_BRANCHES: $("fAutoBranches").value,
      }});
      toast("Opções do Auditor salvas", "success");
    } catch (e) { toast(e.message, "danger"); }
  }, "Salvando…");
});

// Sprint 11 — cron Auditor Fiscal autonomo
async function loadFiscalSchedule() {
  try {
    const r = await api("/api/admin/config/fiscal-schedule");
    const sel = $("fSchedule");
    if (!sel) return;
    sel.innerHTML = (r.options || []).map(o =>
      `<option value="${o.key}" ${o.key === r.current ? "selected" : ""}>${o.label}</option>`
    ).join("");
    const next = $("fScheduleNext");
    if (next) {
      if (r.enabled && r.next_run_time) {
        const d = new Date(r.next_run_time);
        next.textContent = `próxima: ${d.toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" })}`;
      } else {
        next.textContent = "próxima: — (desativada)";
      }
    }
  } catch (e) {
    const sel = $("fSchedule");
    if (sel) sel.innerHTML = `<option value="">(falha ao carregar)</option>`;
  }
}

$("btnSaveSchedule")?.addEventListener("click", async () => {
  await withSpinner($("btnSaveSchedule"), async () => {
    try {
      const r = await api("/api/admin/config/fiscal-schedule", {
        method: "POST", body: { schedule: $("fSchedule").value },
      });
      toast(`Frequência salva: ${r.label}`, "success");
      const next = $("fScheduleNext");
      if (next) {
        if (r.enabled && r.next_run_time) {
          const d = new Date(r.next_run_time);
          next.textContent = `próxima: ${d.toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" })}`;
        } else {
          next.textContent = "próxima: — (desativada)";
        }
      }
    } catch (e) { toast(e.message, "danger"); }
  }, "Salvando…");
});

// ============================================================
//  Aba Catálogo de Erros — carrega markdown e renderiza
// ============================================================
async function loadCatalog() {
  const box = $("catalogBox");
  if (box.dataset.loaded === "1") return;
  box.innerHTML = `<div class="text-muted small p-3">Carregando catálogo…</div>`;
  try {
    const r = await api("/api/admin/error-catalog");
    box.innerHTML = _renderMarkdown(r.content);
    box.dataset.loaded = "1";
  } catch (e) {
    box.innerHTML = `<div class="text-danger small p-3">${e.message}</div>`;
  }
}

// Renderizador markdown bem simples (não precisa de marked.js para esta aba):
// - títulos, tabelas, code, listas, bold, blockquote.
function _renderMarkdown(md) {
  // Tabelas
  md = md.replace(/((?:^\|.*\|\n)+)/gm, (block) => {
    const rows = block.trim().split("\n");
    if (rows.length < 2) return block;
    const headers = rows[0].split("|").slice(1, -1).map(s => s.trim());
    const body = rows.slice(2).map(r => r.split("|").slice(1, -1).map(s => s.trim()));
    let h = '<table class="table table-sm table-bordered table-hover"><thead><tr>';
    h += headers.map(c => `<th>${c}</th>`).join("");
    h += '</tr></thead><tbody>';
    h += body.map(r => "<tr>" + r.map(c => `<td>${c}</td>`).join("") + "</tr>").join("");
    h += '</tbody></table>';
    return h;
  });
  // Cabeçalhos
  md = md.replace(/^# (.+)$/gm, '<h3 class="mt-3">$1</h3>');
  md = md.replace(/^## (.+)$/gm, '<h5 class="mt-4 text-uppercase text-muted small">$1</h5>');
  md = md.replace(/^### (.+)$/gm, '<h6 class="mt-3">$1</h6>');
  // Negrito + código inline
  md = md.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  md = md.replace(/`([^`]+)`/g, '<code>$1</code>');
  // Listas
  md = md.replace(/^- (.+)$/gm, "<li>$1</li>");
  md = md.replace(/(<li>.*<\/li>\n?)+/g, m => `<ul>${m}</ul>`);
  // Blocos de código com triple-backtick
  md = md.replace(/```(\w*)\n([\s\S]*?)```/g, (_m, _lang, code) =>
    `<pre class="bg-light p-2 small"><code>${code.replace(/</g, "&lt;")}</code></pre>`);
  // Quebras
  md = md.split(/\n\n+/).map(p => p.startsWith("<") ? p : `<p>${p.replace(/\n/g, "<br>")}</p>`).join("\n");
  return md;
}

document.querySelector('a[href="#tabCatalog"]').addEventListener("shown.bs.tab", loadCatalog);

// ============================================================
//  Aba Manutenção — reload / restart
// ============================================================
const restartModal = new bootstrap.Modal("#restartModal");

$("btnReload").addEventListener("click", async () => {
  await withSpinner($("btnReload"), async () => {
    try {
      const r = await api("/api/admin/reload-config", { method: "POST" });
      toast(`Reload OK: ${r.reloaded.join(", ")}`, "success");
      if (r.errors?.length) toast("Avisos: " + r.errors.join("; "), "warning");
      refreshStatus();
    } catch (e) { toast(e.message, "danger"); }
  }, "Recarregando…");
});

$("btnRestart").addEventListener("click", () => restartModal.show());

$("confirmRestart").addEventListener("click", async () => {
  restartModal.hide();
  try {
    await api("/api/admin/restart", { method: "POST" });
    toast("Reiniciando…", "warning");
    const start = Date.now();
    const tryAgain = async () => {
      try {
        await fetch("/health");
        toast("Servidor de volta no ar", "success");
        refreshStatus();
      } catch {
        if (Date.now() - start < 30000) setTimeout(tryAgain, 1500);
        else toast("Servidor não voltou em 30s — verifique o supervisor", "danger");
      }
    };
    setTimeout(tryAgain, 3000);
  } catch (e) { toast(e.message, "danger"); }
});

// ============================================================
//  Sprint 6 — SX3 reload
// ============================================================
async function refreshSx3Stats() {
  try {
    const s = await api("/api/admin/sx3/stats");
    $("sx3Stats").textContent = s.loaded
      ? `✅ ${s.total_fields} campos carregados (${s.total_by_field} indices por campo)`
      : `⚠️ SX3 nao carregado — lazy-load na proxima exportacao`;
  } catch (e) {
    $("sx3Stats").textContent = "—";
  }
}

$("btnSx3Reload").addEventListener("click", async () => {
  await withSpinner($("btnSx3Reload"), async () => {
    try {
      const r = await api("/api/admin/sx3/reload", { method: "POST" });
      if (r.loaded) {
        toast(`SX3 recarregado: ${r.count} campos de ${r.source}`, "success");
      } else {
        toast(`SX3 nao carregou: ${r.error || "tabela inacessivel"}`, "warning");
      }
      await refreshSx3Stats();
    } catch (e) { toast(e.message, "danger"); }
  }, "Recarregando…");
});

// ============================================================
//  Sprint 6 — Sessoes Ativas
// ============================================================
function _fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString("pt-BR");
}

function _truncUa(ua, n = 50) {
  if (!ua) return "—";
  return ua.length > n ? ua.slice(0, n) + "…" : ua;
}

function renderSessions(data) {
  const sessions = data.sessions || [];
  const box = $("sessionsBox");
  if (!sessions.length) {
    box.innerHTML = `<div class="text-muted small p-3">Nenhuma sessão para listar.</div>`;
    return;
  }

  // Agrupa por usuario para destacar quem tem multiplas sessoes
  const byUser = {};
  sessions.forEach(s => {
    if (!byUser[s.username]) byUser[s.username] = [];
    byUser[s.username].push(s);
  });

  let html = `
    <table class="table table-sm table-hover align-middle">
      <thead class="table-light">
        <tr>
          <th>Usuário</th>
          <th>IP</th>
          <th>Navegador / User-Agent</th>
          <th>Login em</th>
          <th>Expira em</th>
          <th>Status</th>
          <th style="width:140px">Ação</th>
        </tr>
      </thead>
      <tbody>
  `;
  for (const s of sessions) {
    const multi = byUser[s.username].length > 1;
    const badge = !s.is_active
      ? `<span class="badge bg-secondary">Revogada</span>`
      : (multi
          ? `<span class="badge bg-warning text-dark" title="Múltiplas sessões para esta conta">${byUser[s.username].length}×</span>`
          : `<span class="badge bg-success">Ativa</span>`);
    const userBadge = s.is_self
      ? ` <span class="badge bg-info text-dark ms-1" title="Sua sessão atual">você</span>`
      : "";
    const action = s.is_active
      ? `<button class="btn btn-sm btn-outline-danger btn-revoke" data-jti="${s.jti}" data-user="${s.username}">
           Derrubar
         </button>`
      : `<span class="text-muted small">—</span>`;
    html += `
      <tr>
        <td><strong>${s.username}</strong>${userBadge}<br><span class="small text-muted">${s.full_name || s.email}</span></td>
        <td><code class="small">${s.ip_address || "—"}</code></td>
        <td><span class="small" title="${(s.user_agent || "").replace(/"/g, "&quot;")}">${_truncUa(s.user_agent)}</span></td>
        <td class="small">${_fmtDate(s.created_at)}</td>
        <td class="small">${_fmtDate(s.expires_at)}</td>
        <td>${badge}</td>
        <td>${action}</td>
      </tr>
    `;
  }
  html += `</tbody></table>`;
  box.innerHTML = html;

  // Listeners dos botoes Derrubar
  box.querySelectorAll(".btn-revoke").forEach(btn => {
    btn.addEventListener("click", async () => {
      const jti = btn.dataset.jti;
      const user = btn.dataset.user;
      if (!confirm(`Derrubar a sessão de ${user}? O usuário precisará logar novamente.`)) return;
      btn.disabled = true;
      try {
        await api(`/api/admin/sessions/${encodeURIComponent(jti)}`, { method: "DELETE" });
        toast(`Sessão de ${user} revogada`, "success");
        loadSessions();
      } catch (e) {
        toast(e.message, "danger");
        btn.disabled = false;
      }
    });
  });
}

async function loadSessions() {
  await withSpinner($("btnReloadSessions"), async () => {
    try {
      const include = $("sessIncludeRevoked").checked ? "?include_revoked=true" : "";
      const data = await api(`/api/admin/sessions${include}`);
      renderSessions(data);
    } catch (e) {
      $("sessionsBox").innerHTML = `<div class="text-danger small p-3">${e.message}</div>`;
    }
  }, "Atualizando…");
}

$("btnReloadSessions").addEventListener("click", loadSessions);
$("sessIncludeRevoked").addEventListener("change", loadSessions);
document.querySelector('a[href="#tabSessions"]').addEventListener("shown.bs.tab", loadSessions);
document.querySelector('a[href="#tabMaint"]').addEventListener("shown.bs.tab", refreshSx3Stats);

// ============================================================
//  Sprint 8 Part 3 — Webhook de alertas (Teams/Slack/genérico)
// ============================================================
$("btnSaveWebhook").addEventListener("click", async () => {
  const url = ($("fWebhook").value || "").trim();
  if (url && !/^https?:\/\//i.test(url)) {
    return toast("URL deve começar com http:// ou https://", "warning");
  }
  await withSpinner($("btnSaveWebhook"), async () => {
    try {
      const r = await api("/api/admin/config/webhook", { method: "POST", body: { url } });
      $("fWebhook").value = "";   // limpa pra nao mostrar a URL com token
      const hint = $("fWebhookHint");
      if (hint) {
        hint.innerHTML = r.configured
          ? "✓ Webhook salvo. Use o botão de teste para validar."
          : "⚠️ Webhook removido — alertas críticos desativados.";
      }
      toast(r.detail, "success");
    } catch (e) { toast(e.message, "danger"); }
  }, "Salvando…");
});

$("btnTestWebhook").addEventListener("click", async () => {
  await withSpinner($("btnTestWebhook"), async () => {
    try {
      const r = await api("/api/admin/test/webhook", { method: "POST", body: {} });
      if (r.ok) {
        toast(`✅ Webhook respondeu HTTP ${r.status_code || 200}`, "success");
      } else {
        toast(`❌ ${r.detail}`, "danger");
      }
    } catch (e) { toast(e.message, "danger"); }
  }, "Enviando teste…");
});

// ============================================================
//  Boot
// ============================================================
(async () => {
  await refreshStatus();
  await loadConfig();
  await loadFiscalSchedule();   // Sprint 11
})();

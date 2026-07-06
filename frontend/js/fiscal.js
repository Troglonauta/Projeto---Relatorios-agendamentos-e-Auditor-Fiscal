/* Auditor Fiscal — Sprint 12 (Auditoria Interna).

   Compara SDS/SDT (XML internalizado pelo ERP) vs SF1/SD1 (nota classificada).
   Sem fonte externa, sem teste de conexao, sem aba "Pendentes". As
   "Notas Ausentes" (SDS sem SF1 correspondente) viram anomalia CRITICA
   normal — aparecem na tabela com `field=nota_ausente`.
*/
import { api, auth, toast, withSpinner, formatBR } from "./api.js";
import { renderLayout } from "./layout.js";

// Fix #5 — datas do Protheus vem como YYYYMMDD (ex: 20260612). Exibe DD/MM/AAAA.
function _fmtProtheusDate(v) {
  const s = String(v ?? "").trim();
  if (/^\d{8}$/.test(s)) return `${s.slice(6, 8)}/${s.slice(4, 6)}/${s.slice(0, 4)}`;
  return s || "—";
}

// Sprint 22.4 — Auditor liberado para operador COM a acao "fiscal" (admin sempre).
if (!auth.isAdmin() && !auth.hasAction("fiscal")) { location.href = "protheus.html"; }

renderLayout({ active: "fiscal", title: "Auditor Fiscal" });

const $ = (id) => document.getElementById(id);

document.getElementById("page").innerHTML = `
  <!-- Sprint 12: banner do motor + tolerancias -->
  <div id="engineInfo" class="source-info-banner mb-3">
    <span class="source-icon">🔍</span>
    <span class="source-text">
      Motor: <strong>Auditoria Interna</strong>
      <span class="badge bg-success ms-1">SDS/SDT ⚔ SF1/SD1</span>
    </span>
  </div>

  <div class="d-flex justify-content-between align-items-end mb-3 flex-wrap gap-2">
    <div>
      <div class="text-muted small">Compara o XML internalizado no ERP (SDS/SDT) contra a nota classificada (SF1/SD1).</div>
      <div class="text-muted small">Anomalias críticas viram e-mail automático para o setor fiscal.</div>
    </div>
    <div class="d-flex gap-2">
      <button class="btn btn-success" id="btnFullReport"
              title="Auditoria completa por chave — mostra TODOS os campos (Conforme + Divergentes)">
        📋 Relatório Completo
      </button>
      <button class="btn btn-outline-secondary" id="btnClearResults"
              title="Limpa filtros e tabela de resultados (estado inicial)">
        🧹 Limpar Resultados
      </button>
      <button class="btn btn-primary" id="btnRun">+ Nova auditoria</button>
    </div>
  </div>

  <!-- Sprint 13 — Modal: Relatório Completo (Match Absoluto) -->
  <div class="modal fade" id="fullReportModal" tabindex="-1">
    <div class="modal-dialog modal-xl modal-dialog-scrollable">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title">📋 Relatório Completo de Auditoria</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
        </div>
        <div class="modal-body">
          <form id="fullReportForm" class="row g-2 align-items-end mb-3">
            <div class="col-md-3">
              <label class="form-label small mb-1">Filial</label>
              <select id="frBranch" class="form-select"></select>
            </div>
            <div class="col-md-7">
              <label class="form-label small mb-1">Chave NFe (44 dígitos)</label>
              <input id="frChave" class="form-control" maxlength="44" minlength="44"
                     placeholder="44 dígitos da chave de acesso" required>
            </div>
            <div class="col-md-2">
              <button type="submit" class="btn btn-success w-100">
                <span class="spinner-border spinner-border-sm me-1 d-none" role="status"></span>
                <span class="label">🔍 Auditar</span>
              </button>
            </div>
            <!-- Sprint 17 — toggle "Exibir apenas divergências" no Relatorio Completo (ON por padrão) -->
            <div class="col-md-12 mt-2">
              <div class="form-check form-switch">
                <input class="form-check-input" type="checkbox" id="frOnlyErrors" checked>
                <label class="form-check-label small fw-semibold" for="frOnlyErrors">
                  ⚠ Exibir apenas divergências
                  <span class="text-muted fw-normal">(desligue para ver TODOS os campos auditados, inclusive os "Conforme")</span>
                </label>
              </div>
            </div>
          </form>
          <div id="frSummary" class="alert alert-secondary small d-none"></div>
          <div id="frCounts" class="d-flex gap-2 mb-2 flex-wrap"></div>
          <div id="frContent">
            <div class="text-muted small p-3">
              Informe a chave NFe e clique em <strong>Auditar</strong> para gerar
              o relatório completo. <strong>Todos</strong> os campos serão exibidos
              (Conforme em verde, Divergente em vermelho/amarelo, Sem dado em cinza).
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Fechar</button>
        </div>
      </div>
    </div>
  </div>

  <!-- v2.24 — Painel SEPARADO das decisoes manuais (o que foi alterado x sistema) -->
  <div class="modal fade" id="modalManualDecisions" tabindex="-1">
    <div class="modal-dialog modal-xl modal-dialog-scrollable">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title">📝 Alterações Manuais — auditoria de decisões</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
        </div>
        <div class="modal-body">
          <p class="text-muted small mb-2">
            Painel <strong>separado</strong> das marcações manuais da analista. Os KPIs
            do <strong>sistema</strong> (auditoria automática) ficam no painel principal;
            aqui você vê apenas o que foi <strong>alterado manualmente</strong> e por quem.
            Respeita os filtros de data/filial do painel principal.
          </p>
          <div id="mdKpis" class="kpi-grid mb-3"></div>
          <h6 class="text-uppercase text-muted small mb-1">Por usuário</h6>
          <div id="mdByUser" class="mb-3"></div>
          <h6 class="text-uppercase text-muted small mb-1">Últimas alterações</h6>
          <div class="table-responsive">
            <table class="table table-sm table-bordered fiscal-report-table mb-0">
              <thead><tr>
                <th>Quando</th><th>Filial</th><th>Documento</th>
                <th>Campo</th><th>Novo status</th><th>Alterado por</th>
              </tr></thead>
              <tbody id="mdRecent"></tbody>
            </table>
          </div>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Fechar</button>
        </div>
      </div>
    </div>
  </div>

  <!-- v2.28 — Histórico de auditorias por decêndio -->
  <div class="modal fade" id="modalAuditRuns" tabindex="-1">
    <div class="modal-dialog modal-xl modal-dialog-scrollable">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title">📅 Auditorias por decêndio</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
        </div>
        <div class="modal-body">
          <p class="text-muted small mb-2">
            Cada auditoria que você roda (período selecionado no calendário) fica
            <strong>registrada aqui</strong>. Trabalhe por decêndio (10 dias) e
            depois clique em <strong>Ver documentos</strong> para revisitar
            exatamente o que foi auditado naquele período.
          </p>
          <div class="table-responsive">
            <table class="table table-sm table-bordered fiscal-report-table mb-0">
              <thead><tr>
                <th>Período auditado</th><th>Filiais</th><th>Motor</th>
                <th>Rodada em</th><th>Documentos</th><th>Divergências</th>
                <th>Status</th><th>Por</th><th></th>
              </tr></thead>
              <tbody id="auditRunsRows"><tr><td colspan="9" class="p-3 text-muted">Carregando…</td></tr></tbody>
            </table>
          </div>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Fechar</button>
        </div>
      </div>
    </div>
  </div>

  <!-- v2.28 — banner de drill-down por auditoria (decêndio) -->
  <div id="auditRunBanner" class="alert alert-info py-2 px-3 mb-2 d-none d-flex justify-content-between align-items-center"></div>

  <!-- KPIs -->
  <div class="kpi-grid mb-3">
    <div class="kpi-card"><div class="label">Anomalias (7d)</div><div class="value" id="kpiTotal">—</div></div>
    <div class="kpi-card"><div class="label">Críticas</div><div class="value text-danger" id="kpiCrit">—</div></div>
    <div class="kpi-card"><div class="label">Avisos</div><div class="value text-warning" id="kpiWarn">—</div></div>
    <div class="kpi-card"><div class="label">Filiais afetadas</div><div class="value" id="kpiBranches">—</div></div>
  </div>

  <!-- Filtros -->
  <div class="builder-panel mb-3">
    <div class="row g-2 align-items-end">
      <div class="col-md-2">
        <label class="form-label small mb-1">Desde</label>
        <input id="fFrom" type="date" class="form-control">
      </div>
      <div class="col-md-2">
        <label class="form-label small mb-1">Até</label>
        <input id="fTo" type="date" class="form-control">
      </div>
      <div class="col-md-2">
        <label class="form-label small mb-1">Severidade</label>
        <select id="fSev" class="form-select">
          <option value="">Todas</option>
          <option value="critical">Crítica</option>
          <option value="warn">Aviso</option>
          <option value="info">Info</option>
        </select>
      </div>
      <div class="col-md-2">
        <label class="form-label small mb-1">Filial</label>
        <select id="fBranch" class="form-select"></select>
      </div>
      <div class="col-md-2">
        <button class="btn btn-outline-primary w-100" id="btnFilter">
          <span class="spinner-border spinner-border-sm me-2 d-none" role="status"></span>
          <span class="label">Filtrar</span>
        </button>
      </div>
    </div>
    <!-- v2.25 — filtro por CRUZAMENTO (campo) + situacao (conforme/divergente) -->
    <div class="row g-2 mt-2 align-items-end">
      <div class="col-md-4">
        <label class="form-label small mb-1">Cruzamento (campo)</label>
        <select id="fField" class="form-select">
          <option value="">Todos os cruzamentos</option>
        </select>
      </div>
      <div class="col-md-3">
        <label class="form-label small mb-1">Situação do campo</label>
        <select id="fFieldStatus" class="form-select">
          <option value="divergent">Divergente</option>
          <option value="ok">Conforme</option>
        </select>
      </div>
      <div class="col-md-5">
        <div class="form-text small text-muted">
          Ex.: <strong>Número da NF</strong> + <strong>Conforme</strong> → notas lançadas
          conforme a DANFE; <strong>Divergente</strong> → para revisar.
        </div>
      </div>
    </div>
    <div class="row g-2 mt-2 align-items-center">
      <div class="col-md-4">
        <!-- Sprint 17 — Toggle "Exibir apenas divergências" (ON por padrão) -->
        <div class="form-check form-switch">
          <input class="form-check-input" type="checkbox" id="fOnlyErrors">
          <label class="form-check-label small fw-semibold" for="fOnlyErrors">
            ⚠ Exibir apenas divergências
          </label>
        </div>
      </div>
      <div class="col-md-4">
        <div class="form-check form-switch">
          <input class="form-check-input" type="checkbox" id="fIncludeAcked">
          <label class="form-check-label small text-muted" for="fIncludeAcked">
            Incluir documentos já tratados (marcados como ciente)
          </label>
        </div>
      </div>
      <div class="col-md-4 text-end">
        <button class="btn btn-outline-secondary btn-sm me-2" id="btnAuditRuns"
                title="Histórico de auditorias por decêndio (período auditado)">
          📅 Auditorias (decêndios)
        </button>
        <button class="btn btn-outline-info btn-sm me-2" id="btnManualDecisions"
                title="Painel separado: o que foi alterado manualmente e por quem">
          📝 Alterações manuais
        </button>
        <div class="btn-group">
          <button class="btn btn-success btn-sm dropdown-toggle" data-bs-toggle="dropdown">
            📥 Exportar relatório
          </button>
          <ul class="dropdown-menu dropdown-menu-end">
            <li>
              <a class="dropdown-item exp-anomaly" href="#" data-fmt="xlsx">
                <strong>Excel (.xlsx)</strong>
                <br><small class="text-muted">Header verde Fertimaxi · freeze panes</small>
              </a>
            </li>
            <li><hr class="dropdown-divider"></li>
            <li>
              <a class="dropdown-item exp-anomaly" href="#" data-fmt="csv">CSV</a>
            </li>
          </ul>
        </div>
      </div>
    </div>
  </div>

  <!-- Sprint 18 — Tabela MESTRE (1 linha = 1 documento). Detalhes em modal. -->
  <div class="table-card">
    <div class="table-responsive">
      <table id="tabela_anomalias" class="table table-sm mb-0 align-middle" style="width:100%">
        <thead><tr>
          <th>Auditado em (BRT)</th>
          <th>Filial</th>
          <th>Documento</th>
          <th>Fornecedor</th>
          <th title="Quantidade de campos divergentes neste documento">Divergências</th>
          <th>Ações</th>
        </tr></thead>
        <tbody id="rows"><tr><td colspan="6" class="p-3 text-muted">Carregando…</td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- Sprint 18 + 19 — Modal de detalhamento da nota (campos + tomada de decisao) -->
  <div class="modal fade" id="modalDocumentDetails" tabindex="-1">
    <div class="modal-dialog modal-xl modal-dialog-scrollable">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title">🔍 Divergências do Documento</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
        </div>
        <div class="modal-body">
          <div id="mddSummary" class="alert alert-secondary small d-none"></div>
          <div id="mddCounts" class="d-flex gap-2 mb-2 flex-wrap"></div>
          <div id="mddContent">
            <div class="text-muted small p-3">Carregando…</div>
          </div>

          <!-- v2.20 — Revisao manual do documento (visivel a todos os usuarios)
               v2.27 — oculto por ora a pedido (decisao por campo ja persiste) -->
          <div id="mddReviewBar" class="border-top mt-3 pt-3 d-none">
            <h6 class="text-uppercase text-muted small mb-2">✎ Revisão manual</h6>
            <div id="mddReviewStatus" class="small mb-2"></div>
            <div class="row g-2 align-items-end">
              <div class="col-md-9">
                <label class="form-label small mb-1" for="mddReviewNote">
                  Observação da revisão (opcional — registra na Trilha de Auditoria)
                </label>
                <input type="text" id="mddReviewNote" class="form-control form-control-sm"
                       maxlength="500"
                       placeholder="ex: conferido manualmente — documento em conformidade">
              </div>
              <div class="col-md-3">
                <button type="button" class="btn btn-success btn-sm w-100" id="mddBtnReview">
                  <span class="spinner-border spinner-border-sm me-1 d-none" role="status"></span>
                  <span class="label">✎ Marcar como revisado</span>
                </button>
              </div>
            </div>
            <div class="form-text small text-muted mt-1">
              Registra que <strong>você</strong> revisou este documento. Fica visível
              a todos como <strong>revisado manualmente por você</strong> (inclusive
              em documentos sem divergência) e na trilha
              (<code>fiscal.document.reviewed</code>).
            </div>
          </div>

          <!-- Sprint 19 — Bloco de tomada de decisao (ack do documento) -->
          <div id="mddAckBlock" class="border-top mt-3 pt-3 d-none">
            <h6 class="text-uppercase text-muted small mb-2">📝 Tomada de decisão</h6>
            <div class="row g-2 align-items-end">
              <div class="col-md-9">
                <label class="form-label small mb-1" for="mddAckNote">
                  Justificação / Decisão (opcional — registra na Trilha de Auditoria)
                </label>
                <input type="text" id="mddAckNote" class="form-control form-control-sm"
                       maxlength="500"
                       placeholder="ex: divergência aprovada pelo fiscal — diferença de R$ 0,03 imaterial">
              </div>
              <div class="col-md-3">
                <button type="button" class="btn btn-success btn-sm w-100" id="mddBtnAck">
                  <span class="spinner-border spinner-border-sm me-1 d-none" role="status"></span>
                  <span class="label">✓ Marcar como ciente</span>
                </button>
              </div>
            </div>
            <div class="form-text small text-muted mt-1">
              Marca <strong>todas</strong> as divergências deste documento como cientes.
              A ação fica registrada como <code>fiscal.document.acked</code> na trilha.
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Fechar</button>
        </div>
      </div>
    </div>
  </div>

  <!-- Modal: nova auditoria -->
  <div class="modal fade" id="runModal" tabindex="-1">
    <div class="modal-dialog modal-lg">
      <form class="modal-content" id="runForm">
        <div class="modal-header">
          <h5 class="modal-title">Nova auditoria fiscal</h5>
          <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
        </div>
        <div class="modal-body">
          <div class="border rounded p-2 mb-3 soft-warn">
            <label class="form-label small mb-1">
              <strong>🎯 Chave de Acesso Específica</strong>
              <span class="text-muted">(opcional — se preenchida, ignora o período abaixo)</span>
            </label>
            <input id="rChave" class="form-control" maxlength="44" minlength="44"
                   placeholder="44 dígitos da chave NFe — busca apenas esse documento">
            <div class="form-text">Quando preenchida, o auditor varre <strong>apenas</strong> essa chave.</div>
          </div>

          <div class="row g-2">
            <!-- Sprint 20+21 — seletor de MOTOR (fiscal / financeiro / comercial) -->
            <div class="col-md-12">
              <label class="form-label">Motor de Auditoria</label>
              <select id="rEngine" class="form-select">
                <option value="internal" selected>Motor: Fiscal Interno (SDS/SDT × SF1/SD1)</option>
                <option value="financeiro_se2">Motor: Financeiro (Contas a Pagar SF1 × SE2)</option>
                <option value="comercial_sc5_se1">Motor: Comercial (Pedidos faturados SC5 × SE1)</option>
              </select>
              <div class="form-text" id="rEngineHint">
                <strong>Fiscal Interno:</strong> compara o XML internalizado pelo ERP contra a nota classificada (default).
              </div>
            </div>

            <div class="col-md-6"><label class="form-label">Desde (data)</label>
              <input id="rFrom" type="date" class="form-control"></div>
            <div class="col-md-6"><label class="form-label">Até (data)</label>
              <input id="rTo" type="date" class="form-control"></div>

            <div class="col-md-12">
              <label class="form-label">
                Filiais a auditar
                <small class="text-muted">(Ctrl/Cmd+click para múltipla seleção)</small>
              </label>
              <select id="rBranches" class="form-select" multiple size="4"></select>
              <div class="form-text">Selecione 1 ou mais filiais.</div>
            </div>

            <div class="col-md-12 mt-2">
              <label class="form-label small">Tipos de documento a auditar</label>
              <div class="d-flex gap-3 flex-wrap">
                <div class="form-check">
                  <input class="form-check-input rDocModel" type="checkbox" value="55" id="rNFe" checked>
                  <label class="form-check-label" for="rNFe">NF-e (modelo 55)</label>
                </div>
                <div class="form-check">
                  <input class="form-check-input rDocModel" type="checkbox" value="57" id="rCTe">
                  <label class="form-check-label" for="rCTe">CT-e (modelo 57)</label>
                </div>
                <div class="form-check">
                  <input class="form-check-input rDocModel" type="checkbox" value="65" id="rNFCe">
                  <label class="form-check-label" for="rNFCe">NFC-e (modelo 65)</label>
                </div>
                <div class="form-check">
                  <input class="form-check-input rDocModel" type="checkbox" value="58" id="rMDFe">
                  <label class="form-check-label" for="rMDFe">MDF-e (modelo 58)</label>
                </div>
                <div class="form-check">
                  <input class="form-check-input rDocModel" type="checkbox" value="" id="rAllModels">
                  <label class="form-check-label text-muted" for="rAllModels">Todos (ignorar filtro)</label>
                </div>
              </div>
            </div>
          </div>
          <div class="form-text mt-3">A auditoria roda em background — o resultado vem por e-mail e aparece na lista.</div>
        </div>
        <div class="modal-footer">
          <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancelar</button>
          <button type="submit" class="btn btn-primary" id="rRun">
            <span class="spinner-border spinner-border-sm me-2 d-none" role="status"></span>
            <span class="label">Enfileirar</span>
          </button>
        </div>
      </form>
    </div>
  </div>
`;

const runModal = new bootstrap.Modal("#runModal");

const SEV_BADGE = {
  critical: '<span class="badge bg-danger">crítica</span>',
  warn:     '<span class="badge bg-warning text-dark">aviso</span>',
  info:     '<span class="badge bg-info">info</span>',
};

// Cache da info do motor (tolerancias)
let engineInfo = null;

// Sprint 12 Hotfix — DataTables (CDN dinamico) + estado da tabela
let dataTable = null;            // instancia atual do DataTable
let currentAnomalies = [];       // ultimas anomalias carregadas (p/ Limpar)
let _dtLibsLoading = null;       // promise de boot do DataTables

function _loadDataTablesLibs() {
  // Carrega jQuery + DataTables (Bootstrap 5) via CDN. Idempotente.
  if (_dtLibsLoading) return _dtLibsLoading;
  _dtLibsLoading = (async () => {
    if (!window.jQuery) {
      await new Promise((resolve, reject) => {
        const s = document.createElement("script");
        s.src = "https://code.jquery.com/jquery-3.7.1.min.js";
        s.onload = resolve; s.onerror = reject;
        document.head.appendChild(s);
      });
    }
    if (!window.jQuery.fn || !window.jQuery.fn.DataTable) {
      // CSS DataTables Bootstrap 5
      const css = document.createElement("link");
      css.rel = "stylesheet";
      css.href = "https://cdn.datatables.net/2.1.8/css/dataTables.bootstrap5.min.css";
      document.head.appendChild(css);
      await new Promise((resolve, reject) => {
        const s = document.createElement("script");
        s.src = "https://cdn.datatables.net/2.1.8/js/dataTables.min.js";
        s.onload = resolve; s.onerror = reject;
        document.head.appendChild(s);
      });
      await new Promise((resolve, reject) => {
        const s = document.createElement("script");
        s.src = "https://cdn.datatables.net/2.1.8/js/dataTables.bootstrap5.min.js";
        s.onload = resolve; s.onerror = reject;
        document.head.appendChild(s);
      });
    }
  })();
  return _dtLibsLoading;
}

function _tolerancesByField() {
  if (!engineInfo) return {};
  return {
    valor_total:    engineInfo.tolerance.valor_rs,
    base_icms:      engineInfo.tolerance.icms_rs,
    valor_icms:     engineInfo.tolerance.icms_rs,
    icms_aliquota:  "0,01%",
    icms_valor:     engineInfo.tolerance.icms_rs,
    icms_base:      engineInfo.tolerance.icms_rs,
    quantidade:     engineInfo.tolerance.quantidade,
    valor_unit:     engineInfo.tolerance.valor_rs,
    frete_seguro:   engineInfo.tolerance.valor_rs,
    desconto:       engineInfo.tolerance.valor_rs,
    outras_despesas:engineInfo.tolerance.valor_rs,
    ncm:            "0 (sem tolerância)",
    cfop:           "0 (sem tolerância)",
    cst:            "0 (sem tolerância)",
    nota_ausente:   "—",
  };
}

function _tooltipFor(field) {
  const tol = _tolerancesByField();
  const f = (field || "").toLowerCase();
  for (const [token, value] of Object.entries(tol)) {
    if (f.includes(token)) {
      return `Tolerância aplicada: ${value}\nCampo técnico: ${field}`;
    }
  }
  return `Campo técnico: ${field}`;
}

async function loadKpis() {
  try {
    const s = await api("/api/fiscal/summary?days=7");
    $("kpiTotal").textContent = s.total;
    $("kpiCrit").textContent = s.critical;
    $("kpiWarn").textContent = s.warn;
    $("kpiBranches").textContent = Object.keys(s.by_branch || {}).length;
  } catch (e) { /* silencioso */ }
}

function _rowHtml(g) {
  // Sprint 18 — `g` agora representa UM DOCUMENTO agregado (não mais campo).
  const qtd  = g.qtd_divergencias || 0;
  const crit = g.qtd_critical || 0;
  const warnQtd = g.qtd_warn || 0;
  const hasCrit = crit > 0;
  const badgeClass = hasCrit ? "bg-danger" : (warnQtd > 0 ? "bg-warning text-dark" : "bg-secondary");
  const rowClass = hasCrit ? "ncm-row" : "";
  const chave = g.doc_key || "";
  const chaveShort = chave.length >= 8 ? `…${chave.slice(-8)}` : (chave || "(sem chave)");
  // Fix #4 — documento auditado SEM divergencia aparece como "OK" (verde).
  const qtdCell = qtd === 0
    ? `<span class="badge bg-success">OK</span>`
    : `<span class="badge ${badgeClass}">${qtd}</span>` +
      (hasCrit ? ` <small class="text-danger">(${crit} crítica${crit > 1 ? "s" : ""})</small>` : "");
  const actionsCell =
    `<button class="btn btn-sm btn-primary btn-detalhes" ` +
    `data-chave="${chave.replace(/"/g, '&quot;')}" ` +
    `data-branch="${(g.branch || '').replace(/"/g, '&quot;')}">` +
    `🔍 Detalhes</button>`;
  // v2.20 — selo "revisado manualmente" visivel a todos na lista mestre.
  const reviewedBadge = g.reviewed_by
    ? `<br><span class="badge bg-info text-dark" title="Revisado manualmente por ` +
      `${(g.reviewed_by || '').replace(/"/g, '&quot;')}` +
      `${g.reviewed_at ? ' em ' + formatBR(g.reviewed_at) : ''}">✎ revisado</span>`
    : "";
  return {
    rowClass,
    tooltip: `Documento ${chaveShort} — ${qtd} divergência(s)`,
    cells: [
      `<small>${formatBR(g.audited_at)}</small>`,
      g.branch || "-",
      `<code title="${chave}">${chaveShort}</code>${reviewedBadge}`,
      `<small class="text-muted">${g.supplier_cnpj || "—"}</small>`,
      qtdCell,
      actionsCell,
    ],
  };
}

// v2.28 — drill-down por auditoria (decendio). Quando setado, a lista mestre
// mostra so os documentos daquela execucao (job), ignorando filtros de data.
let _jobFilter = null;   // { jobId, label } | null
const _fmtISOdate = (s) => {
  if (!s) return "—";
  const p = String(s).slice(0, 10).split("-");
  return p.length === 3 ? `${p[2]}/${p[1]}/${p[0]}` : s;
};
function _renderAuditRunBanner() {
  const el = $("auditRunBanner");
  if (!el) return;
  if (_jobFilter && _jobFilter.jobId) {
    el.innerHTML =
      `<span>📅 Exibindo a auditoria do decêndio <strong>${_jobFilter.label}</strong>` +
      ` — filtros de data desativados.</span>` +
      `<button class="btn btn-sm btn-outline-dark" id="btnClearJobFilter">Limpar</button>`;
    el.classList.remove("d-none");
    const b = $("btnClearJobFilter");
    if (b) b.onclick = () => { _jobFilter = null; loadAnomalies(); };
  } else {
    el.classList.add("d-none");
    el.innerHTML = "";
  }
}

// v2.25 — popula o dropdown de cruzamentos (campos com divergencia) respeitando
// data/filial; preserva a selecao atual.
async function _loadAnomalyFields() {
  const sel = $("fField");
  if (!sel) return;
  const params = new URLSearchParams();
  if ($("fFrom").value) params.set("date_from", $("fFrom").value);
  if ($("fTo").value)   params.set("date_to",   $("fTo").value);
  if ($("fBranch").value) params.set("branch",  $("fBranch").value);
  const prev = sel.value;
  try {
    const d = await api("/api/fiscal/anomaly-fields?" + params.toString());
    const opts = ['<option value="">Todos os cruzamentos</option>']
      .concat((d.fields || []).map(f =>
        `<option value="${f.value}">${f.label}</option>`));
    sel.innerHTML = opts.join("");
    if (prev && [...sel.options].some(o => o.value === prev)) sel.value = prev;
  } catch { /* silencioso — filtro opcional */ }
}

async function loadAnomalies() {
  const params = new URLSearchParams();
  if ($("fFrom").value) params.set("date_from", $("fFrom").value);
  if ($("fTo").value)   params.set("date_to",   $("fTo").value);
  if ($("fSev").value)  params.set("severity",  $("fSev").value);
  if ($("fBranch").value) params.set("branch",  $("fBranch").value);
  if ($("fIncludeAcked").checked) params.set("include_acked", "true");
  // Sprint 17 — toggle "Exibir apenas divergências" (default ON)
  const onlyErrors = $("fOnlyErrors") ? $("fOnlyErrors").checked : true;
  params.set("only_errors", onlyErrors ? "true" : "false");
  // v2.25 — filtro por cruzamento (campo) + situacao
  if ($("fField") && $("fField").value) {
    params.set("field", $("fField").value);
    params.set("field_status", $("fFieldStatus") ? $("fFieldStatus").value : "divergent");
  }
  // v2.28 — drill-down por auditoria (decendio): so os docs daquela rodada
  if (_jobFilter && _jobFilter.jobId) {
    params.delete("date_from");
    params.delete("date_to");
    params.set("job_id", _jobFilter.jobId);
  }
  _renderAuditRunBanner();
  // Sprint 18: paginacao server-side disponivel, mas como mestre eh 1 doc por linha,
  // o volume cai muito vs Sprint 17 (campos). Limit alto cobre o uso normal.
  params.set("limit", "10000");

  try {
    await _loadDataTablesLibs();
    // Sprint 18 — endpoint AGRUPADO (1 linha = 1 documento)
    const data = await api("/api/fiscal/grouped-anomalies?" + params.toString());
    let items = (data && data.items) || [];
    currentAnomalies = items;

    const $jq = window.jQuery;
    const TABLE_SEL = "#tabela_anomalias";

    // Pattern oficial DataTables — destroi instancia anterior
    if ($jq.fn.DataTable.isDataTable(TABLE_SEL)) {
      $jq(TABLE_SEL).DataTable().clear().destroy();
    }
    dataTable = null;

    const tbody = $("rows");
    if (!items.length) {
      tbody.innerHTML = `<tr><td colspan="6" class="p-3 text-muted">Nenhum documento com divergência no filtro</td></tr>`;
      return;
    }
    tbody.innerHTML = items.map(g => {
      const r = _rowHtml(g);
      return `<tr class="${r.rowClass}" title="${r.tooltip.replace(/"/g, '&quot;')}" ` +
             `data-chave="${(g.doc_key || '').replace(/"/g, '&quot;')}" ` +
             `data-branch="${(g.branch || '').replace(/"/g, '&quot;')}">` +
             r.cells.map(c => `<td>${c}</td>`).join("") +
             `</tr>`;
    }).join("");

    // Paginacao real [10, 20, 50, 100, Todos]
    dataTable = $jq(TABLE_SEL).DataTable({
      paging: true,
      // Sprint 22.3 — paginacao em CIMA e EMBAIXO (l=tamanho, p=paginacao, i=info).
      dom: '<"dt-top d-flex justify-content-between align-items-center flex-wrap gap-2"lp>'
         + 'rt'
         + '<"dt-bottom d-flex justify-content-between align-items-center flex-wrap gap-2"ip>',
      lengthMenu: [[10, 20, 50, 100, -1], [10, 20, 50, 100, "Todos"]],
      pageLength: 20,
      order: [[0, "desc"]],
      searching: false,
      info: true,
      // Sprint 18: coluna "Ações" nao deve ordenar
      columnDefs: [{ orderable: false, targets: [5] }],
      language: {
        lengthMenu: "Mostrar _MENU_ registros por página",
        info: "Mostrando _START_ a _END_ de _TOTAL_ documentos",
        infoEmpty: "0 documentos",
        infoFiltered: "(filtrado de _MAX_ total)",
        paginate: { previous: "Anterior", next: "Próximo", first: "Primeiro", last: "Último" },
        zeroRecords: "Nenhum documento encontrado",
        emptyTable: "Nenhum documento encontrado",
      },
    });

    // Sprint 18 — listener do botao "🔍 Detalhes" (delegado no tbody)
    $jq(TABLE_SEL + " tbody").off("click.detalhes").on("click.detalhes", ".btn-detalhes", function (ev) {
      ev.stopPropagation();
      const chave = this.dataset.chave || "";
      const branch = this.dataset.branch || "";
      if (!chave || !branch) {
        return toast("Documento sem chave/filial para detalhar", "warning");
      }
      openDocumentDetailsModal(chave, branch);
    });
  } catch (e) { toast("Falha ao listar: " + e.message, "danger"); }
}

async function loadEngineInfo() {
  // Carrega engineInfo (tolerancias usadas internamente no relatorio detalhado).
  // O banner de "Tolerância/NCM" no topo foi removido a pedido (sem dizeres na UI).
  try {
    engineInfo = await api("/api/fiscal/engine-info");
  } catch {
    engineInfo = null;
  }
}

$("btnFilter").addEventListener("click", () => withSpinner($("btnFilter"), async () => {
  _jobFilter = null;   // v2.28 — filtrar por data sai do drill-down de decêndio
  await loadAnomalies();
}, "Filtrando…"));

$("btnClearResults").addEventListener("click", async () => {
  // Sprint 20 Bug 2 — Limpar Resultados agora e' DESTRUTIVO de verdade.
  // Apaga fisicamente as anomalias do SQLite via DELETE /api/fiscal/purge.
  // Antes, so limpava o DOM/DataTables — as anomalias ressuscitavam ao
  // recarregar a pagina porque vinham do banco.

  const confirmText =
    "⚠ Esta ação apaga TODAS as anomalias fiscais do banco de dados.\n\n" +
    "Os documentos auditados precisarão de uma nova auditoria para reaparecer.\n\n" +
    "Continuar?";
  if (!window.confirm(confirmText)) {
    return;
  }

  const btn = $("btnClearResults");
  await withSpinner(btn, async () => {
    try {
      const r = await api("/api/fiscal/purge", { method: "DELETE" });
      toast(
        `${r.deleted_anomalies} anomalia(s) apagada(s) do banco`,
        "success",
      );
    } catch (err) {
      toast("Falha ao limpar: " + err.message, "danger");
      return;
    }

    // Reseta filtros + UI (mesmo que antes faziamos so localmente)
    ["fFrom","fTo","fBranch"].forEach(id => {
      const el = $(id);
      if (el) {
        el.value = "";
        try { el.dispatchEvent(new Event("input", { bubbles: true })); } catch {}
      }
    });
    const sevSel = $("fSev");
    if (sevSel) {
      sevSel.value = "";
      try { sevSel.dispatchEvent(new Event("change", { bubbles: true })); } catch {}
    }
    const ack = $("fIncludeAcked"); if (ack) ack.checked = false;
    const oe = $("fOnlyErrors"); if (oe) oe.checked = false;  // v2.28 — padrao: validar tudo

    // Destroi DataTable com pattern oficial
    const $jq = window.jQuery;
    const TABLE_SEL = "#tabela_anomalias";
    if ($jq && $jq.fn && $jq.fn.DataTable && $jq.fn.DataTable.isDataTable(TABLE_SEL)) {
      $jq(TABLE_SEL).DataTable().clear().destroy();
    }
    if ($jq) {
      try { $jq(TABLE_SEL + " tbody").off("click.detalhes"); } catch {}
    }
    dataTable = null;
    currentAnomalies = [];
    engineInfo = null;

    // Recarrega a tabela mestre — deve vir vazia agora
    try {
      await loadKpis();
      await loadAnomalies();
    } catch (err) {
      // Falha na recarga nao deve mostrar erro destrutivo
      console.warn("Falha ao recarregar pos-purge:", err);
    }
  }, "Limpando…");
});

async function _populateBranchesMultiSelect() {
  const sel = $("rBranches");
  if (!sel) return;
  let branches = window._publicSettingsBranches;
  if (!Array.isArray(branches) || !branches.length) {
    try {
      const cfg = await (await import("./api.js")).loadPublicSettings();
      branches = Array.isArray(cfg.branches) && cfg.branches.length
        ? cfg.branches : ["01","02","03","04","05","06","07","08"];
      window._publicSettingsBranches = branches;
    } catch {
      branches = ["01","02","03","04","05","06","07","08"];
    }
  }
  sel.innerHTML = branches.map(b => `<option value="${b}">Filial ${b}</option>`).join("");
}

async function _populateBranchFilter() {
  // Filial do FILTRO principal: dropdown selecionavel, pre-selecionado em "01".
  const sel = $("fBranch");
  if (!sel) return;
  let branches = window._publicSettingsBranches;
  if (!Array.isArray(branches) || !branches.length) {
    try {
      const cfg = await (await import("./api.js")).loadPublicSettings();
      branches = Array.isArray(cfg.branches) && cfg.branches.length
        ? cfg.branches : ["01","02","03","04","05","06","07","08"];
      window._publicSettingsBranches = branches;
    } catch {
      branches = ["01","02","03","04","05","06","07","08"];
    }
  }
  sel.innerHTML = ['<option value="">Todas</option>']
    .concat(branches.map(b => `<option value="${b}">${b}</option>`)).join("");
  sel.value = branches.includes("01") ? "01" : "";   // default Filial 01
}

$("btnRun").addEventListener("click", async () => {
  const today = new Date().toISOString().slice(0, 10);
  $("rFrom").value = today;
  $("rTo").value = today;
  await _populateBranchesMultiSelect();
  Array.from($("rBranches").options).forEach(o => o.selected = false);
  $("rChave").value = "";
  runModal.show();
});

$("runForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("rRun");
  await withSpinner(btn, async () => {
    const chave = ($("rChave").value || "").trim();
    const selectedBranches = Array.from($("rBranches").selectedOptions || [])
      .map(o => o.value).filter(Boolean);
    // Sprint 20 — motor escolhido (default: internal)
    const engine = $("rEngine")?.value || "internal";
    const payload = {
      date_from: $("rFrom").value,
      date_to:   $("rTo").value,
      branches:  selectedBranches,
      chave_filter: chave || null,
      engine: engine,
    };

    // Sprint 20+21 — motores nao-fiscais nao usam chave NFe nem modelos de documento
    if (engine === "financeiro_se2" || engine === "comercial_sc5_se1") {
      payload.chave_filter = null;
      // doc_models nao se aplica
    } else if (!$("rAllModels").checked) {
      const models = Array.from(document.querySelectorAll(".rDocModel:checked"))
        .map(cb => cb.value).filter(Boolean);
      if (models.length) payload.doc_models = models;
    }

    if (chave) {
      if (chave.length !== 44 || !/^\d+$/.test(chave)) {
        return toast("Chave de Acesso deve ter 44 dígitos numéricos", "warning");
      }
      if (!payload.date_from) payload.date_from = new Date().toISOString().slice(0, 10);
      if (!payload.date_to)   payload.date_to   = payload.date_from;
      if (!payload.branches.length) payload.branches = ["01"];
    } else {
      if (!payload.date_from || !payload.date_to) {
        return toast("Informe o período (Desde/Até) ou uma chave específica", "warning");
      }
      if (!payload.branches.length) {
        return toast("Informe pelo menos uma filial (ou uma chave específica)", "warning");
      }
    }

    try {
      const r = await api("/api/fiscal/audit/run", { method: "POST", body: payload });
      runModal.hide();
      _trackAuditJob(r.job_id);
    } catch (err) { toast(err.message, "danger"); }
  }, "Enfileirando…");
});

// Sprint 20+21 — feedback ao trocar de motor
$("rEngine")?.addEventListener("change", () => {
  const v = $("rEngine").value;
  const hint = $("rEngineHint");
  if (!hint) return;
  if (v === "financeiro_se2") {
    hint.innerHTML = `<strong>Financeiro (Contas a Pagar):</strong> cruza NF (SF1) ` +
                     `vs Títulos (SE2). Regras: Ausência de Título, Valor, Data de Emissão, IRRF, ISS. ` +
                     `Chave NFe e modelos de documento são <em>ignorados</em>.`;
  } else if (v === "comercial_sc5_se1") {
    hint.innerHTML = `<strong>Comercial (Pedidos faturados):</strong> cruza Pedido (SC5) ` +
                     `vs Títulos a Receber (SE1). Regras: Ausência de Título, ` +
                     `Vendedor (comissionamento), Prazo de pagamento, Desconto financeiro. ` +
                     `Apenas pedidos <code>C5_NOTA</code> preenchidos entram. ` +
                     `Chave NFe e modelos de documento são <em>ignorados</em>.`;
  } else {
    hint.innerHTML = `<strong>Fiscal Interno:</strong> compara o XML internalizado pelo ERP contra a nota classificada (default).`;
  }
});

document.querySelector("#rAllModels")?.addEventListener("change", (e) => {
  if (e.target.checked) {
    document.querySelectorAll(".rDocModel").forEach(cb => {
      if (cb.id !== "rAllModels") cb.checked = false;
    });
  }
});
document.querySelectorAll(".rDocModel").forEach(cb => {
  if (cb.id === "rAllModels") return;
  cb.addEventListener("change", () => {
    if (cb.checked) {
      const all = document.querySelector("#rAllModels");
      if (all) all.checked = false;
    }
  });
});


// ============================================================
//  Sprint 13 — Relatório Completo de Documento (Match Absoluto)
// ============================================================
const fullReportModal = new bootstrap.Modal("#fullReportModal");

const STATUS_BADGE = {
  ok:        '<span class="badge bg-success">✓ Conforme</span>',
  divergent: '<span class="badge bg-danger">✗ Divergente</span>',
  skipped:   '<span class="badge bg-secondary">— Sem dado</span>',
};

const STATUS_ROW_CLASS = {
  ok:        "table-success",
  divergent: "table-danger",
  skipped:   "table-light text-muted",
};

// Cores do <select> de status editavel (a analista decide manualmente o ponto).
const STATUS_SELECT_CLASS = {
  ok:        "border-success text-success",
  divergent: "border-danger text-danger fw-semibold",
  skipped:   "text-muted",
};

// <select> de status por linha — a analista pode SOBRESCREVER o status sugerido
// pelo motor (Conforme / Divergente / Sem dado). Recontagem ao vivo via JS.
function _statusSelect(st) {
  const opt = (v, l) => `<option value="${v}"${v === st ? " selected" : ""}>${l}</option>`;
  return `<select class="form-select form-select-sm rf-status-edit ${STATUS_SELECT_CLASS[st] || ""}"
                  title="Status sugerido pelo motor — você pode alterar manualmente">
    ${opt("ok", "✓ Conforme")}${opt("divergent", "✗ Divergente")}${opt("skipped", "— Sem dado")}
  </select>`;
}

async function _populateFullReportBranches() {
  const sel = $("frBranch");
  if (!sel) return;
  let branches = window._publicSettingsBranches;
  if (!Array.isArray(branches) || !branches.length) {
    try {
      const cfg = await (await import("./api.js")).loadPublicSettings();
      branches = Array.isArray(cfg.branches) && cfg.branches.length
        ? cfg.branches : ["01","02","03","04","05","06","07","08"];
      window._publicSettingsBranches = branches;
    } catch {
      branches = ["01","02","03","04","05","06","07","08"];
    }
  }
  sel.innerHTML = branches.map(b => `<option value="${b}">Filial ${b}</option>`).join("");
}

// Sprint 18 — render generico de relatorio (reusado por Relatorio Completo + Detalhes)
function _renderReportInto(data, opts) {
  const { summaryId, countsId, contentId } = opts || {};
  const { summary, counts, report } = data || {};

  // Header com sumario
  const sum = document.getElementById(summaryId);
  if (sum && summary) {
    sum.classList.remove("d-none");
    sum.innerHTML = `
      <div class="row g-2 small">
        <div class="col-12">
          <strong>Chave:</strong>
          <code style="word-break:break-all; white-space:normal">${summary.chave || "—"}</code>
        </div>
        <div class="col-md-3"><strong>Doc/Série:</strong> ${summary.ds_doc || "—"} / ${summary.ds_serie || "—"}</div>
        <div class="col-md-3"><strong>Emissão:</strong> <span class="badge bg-info text-dark">${_fmtProtheusDate(summary.ds_emissa)}</span></div>
        <div class="col-md-3"><strong>Digitação:</strong> <span class="badge bg-warning text-dark">${_fmtProtheusDate(summary.f1_dtdigit)}</span></div>
        <div class="col-md-3"><strong>Filial:</strong> ${summary.branch}</div>
        <div class="col-md-6"><strong>Fornecedor:</strong> ${summary.fornecedor || "(SA2 não localizado)"}</div>
        <div class="col-md-6 mt-1">
          ${summary.nota_classificada
            ? `<span class="badge bg-success">✓ Classificada</span> DOC: <code>${summary.f1_doc || "?"}</code>`
            : `<span class="badge bg-danger">⚠ NÃO classificada</span> (Nota Ausente)`}
          · <span class="text-muted">Doc. de entrada: ${summary.items_sd1} itens · XML: ${summary.items_sdt} itens</span>
        </div>
      </div>
    `;
  }

  // Contadores — counts sempre reflete o universo completo (Sprint 17)
  const cnt = document.getElementById(countsId);
  if (cnt && counts) {
    const filteredHint = (counts.only_errors && counts.filtered !== undefined && counts.filtered !== counts.total)
      ? ` <span class="badge bg-info text-dark">${counts.filtered} exibido(s)</span>`
      : "";
    cnt.innerHTML = `
      <span class="badge bg-secondary">${counts.total} campos auditados</span>
      <span class="badge bg-success">${counts.ok} Conforme</span>
      <span class="badge bg-danger">${counts.divergent} Divergentes</span>
      <span class="badge bg-light text-dark border">${counts.skipped} Sem dado</span>
      ${filteredHint}
    `;
  }

  // Agrupar por categoria (header / item)
  const header = (report || []).filter(r => r.category === "header");
  const itemsByN = {};
  for (const r of (report || [])) {
    if (r.category !== "item") continue;
    const k = r.item_n || "?";
    (itemsByN[k] = itemsByN[k] || []).push(r);
  }

  const buildRow = (r) => {
    const search = `${r.label || ""} ${r.protheus_value || ""} ${r.xml_value || ""} ${r.note || ""}`
      .toLowerCase().replace(/"/g, "&quot;");
    const fk = (r.field_key || "").replace(/"/g, "&quot;");
    // v2.24 — quem alterou este campo por ultimo (persistido)
    const decided = r.decided_by
      ? `<div class="rf-decided small text-info mt-1" title="${r.decided_at ? formatBR(r.decided_at) : ''}">✎ ${r.decided_by}</div>`
      : `<div class="rf-decided small text-info mt-1"></div>`;
    return `
    <tr class="${STATUS_ROW_CLASS[r.status] || ""}" data-rf-status="${r.status}" data-rf-search="${search}" data-rf-label="${(r.label || '').toLowerCase().replace(/"/g, '&quot;')}" data-field-key="${fk}">
      <td><small title="${r.field_ref || r.field || ""}">${r.label}</small></td>
      <td><small><code>${(r.protheus_value || "—").substring(0, 60)}</code></small></td>
      <td><small><code>${(r.xml_value || "—").substring(0, 60)}</code></small></td>
      <td>${_statusSelect(r.status)}${decided}</td>
      <td><small class="text-muted">${r.note || ""}</small></td>
    </tr>`;
  };

  const tableHead = `
    <thead class="table-light">
      <tr>
        <th style="width:25%">Campo</th>
        <th style="width:25%">Protheus (ERP)</th>
        <th style="width:25%">XML (Físico)</th>
        <th style="width:10%">Status/Gravidade</th>
        <th>Observação</th>
      </tr>
    </thead>`;

  const hasHeader = header.length > 0;
  const itemNumbers = Object.keys(itemsByN).sort((a, b) => parseInt(a) - parseInt(b));
  const hasItems = itemNumbers.length > 0;

  let html = "";
  if (hasHeader) {
    html += `<h6 class="text-uppercase text-muted small mt-3 mb-1">Cabeçalho</h6>
      <div class="table-responsive">
        <table class="table table-sm table-bordered mb-3 fiscal-report-table">
          ${tableHead}
          <tbody>${header.map(buildRow).join("")}</tbody>
        </table>
      </div>`;
  }
  for (const n of itemNumbers) {
    html += `<h6 class="text-uppercase text-muted small mt-3 mb-1">Item ${n}</h6>
      <div class="table-responsive">
        <table class="table table-sm table-bordered mb-3 fiscal-report-table">
          ${tableHead}
          <tbody>${itemsByN[n].map(buildRow).join("")}</tbody>
        </table>
      </div>`;
  }
  if (!hasHeader && !hasItems) {
    html = `<div class="alert alert-success small">
      🎉 Nenhuma divergência encontrada para este documento (com o filtro atual).
    </div>`;
  } else {
    // S23.2 — barra de filtro: a analista escolhe os pontos a cruzar (busca por
    // campo/valor) e filtra por status, sem sair do sistema.
    html = `
      <div class="d-flex gap-2 align-items-center mb-2 flex-wrap report-filterbar">
        <select class="form-select form-select-sm report-filter-field" style="max-width:320px">
          <option value="">Todos os campos</option>
          ${[...new Set((report || []).map(r => r.label).filter(Boolean))]
              .sort((a, b) => String(a).localeCompare(String(b)))
              .map(l => `<option value="${String(l).toLowerCase().replace(/"/g, "&quot;")}">${l}</option>`)
              .join("")}
        </select>
        <div class="btn-group btn-group-sm report-status-filter" role="group">
          <button type="button" class="btn btn-outline-secondary active" data-st="all">Todos</button>
          <button type="button" class="btn btn-outline-danger" data-st="divergent">Divergentes</button>
          <button type="button" class="btn btn-outline-success" data-st="ok">Conforme</button>
          <button type="button" class="btn btn-outline-secondary" data-st="skipped">Sem dado</button>
        </div>
        <span class="text-muted small report-filter-count"></span>
      </div>` + html;
  }
  const content = document.getElementById(contentId);
  if (content) {
    content.innerHTML = html;
    _attachReportFilter(content, document.getElementById(countsId),
                        { chave: opts.docKey, branch: opts.branch });
  }
}

// Recontagem ao vivo dos contadores do topo a partir dos status ATUAIS das
// linhas (inclui marcacoes manuais da analista). Acrescenta um aviso de revisao.
function _recountReport(container, countsEl) {
  if (!countsEl) return;
  const rows = container.querySelectorAll("tr[data-rf-status]");
  let ok = 0, dv = 0, sk = 0;
  rows.forEach(tr => {
    const s = tr.getAttribute("data-rf-status");
    if (s === "ok") ok++; else if (s === "divergent") dv++; else sk++;
  });
  countsEl.innerHTML = `
    <span class="badge bg-secondary">${rows.length} campos auditados</span>
    <span class="badge bg-success">${ok} Conforme</span>
    <span class="badge bg-danger">${dv} Divergentes</span>
    <span class="badge bg-light text-dark border">${sk} Sem dado</span>
    <span class="badge bg-info text-dark" title="Alterações de status são salvas automaticamente por campo">✎ alterado manualmente (salvo)</span>
  `;
}

// S23.2 — filtro client-side do relatorio (busca textual + status), por container.
// v2.19 — tambem liga os <select> de status editavel (recontagem + re-filtro).
function _attachReportFilter(container, countsEl, docCtx) {
  const dc = docCtx || {};
  const fieldSel = container.querySelector(".report-filter-field");
  const btns = [...container.querySelectorAll(".report-status-filter [data-st]")];
  const countEl = container.querySelector(".report-filter-count");
  let status = "all";
  const apply = () => {
    // v2.27 — filtro por CAIXA DE SELECAO (campo) em vez de texto digitado.
    const fv = (fieldSel?.value || "");
    const rows = container.querySelectorAll("tr[data-rf-status]");
    let shown = 0;
    rows.forEach(tr => {
      const okStatus = status === "all" || tr.getAttribute("data-rf-status") === status;
      const okField = !fv || (tr.getAttribute("data-rf-label") || "") === fv;
      const vis = okStatus && okField;
      tr.style.display = vis ? "" : "none";
      if (vis) shown++;
    });
    if (countEl) countEl.textContent = `${shown} ponto(s) exibido(s)`;
  };
  if (fieldSel) fieldSel.addEventListener("change", apply);
  btns.forEach(b => b.addEventListener("click", () => {
    btns.forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    status = b.getAttribute("data-st");
    apply();
  }));

  // v2.19/v2.24 — marcacao manual de status: atualiza a linha, reconta o topo,
  // re-filtra e PERSISTE a decisao por campo (quem alterou por ultimo).
  container.querySelectorAll(".rf-status-edit").forEach(sel => {
    sel.addEventListener("change", async () => {
      const tr = sel.closest("tr");
      if (!tr) return;
      const ns = sel.value;
      tr.setAttribute("data-rf-status", ns);
      tr.className = STATUS_ROW_CLASS[ns] || "";
      sel.className = `form-select form-select-sm rf-status-edit ${STATUS_SELECT_CLASS[ns] || ""}`;
      _recountReport(container, countsEl);
      apply();
      const fk = tr.getAttribute("data-field-key");
      if (dc.chave && dc.branch && fk) {
        try {
          const r = await api("/api/fiscal/field-decision", {
            method: "POST",
            body: { doc_key: dc.chave, branch: dc.branch, field_key: fk, status: ns },
          });
          const note = tr.querySelector(".rf-decided");
          if (note) {
            note.textContent = `✎ ${r.decided_by}`;
            note.title = r.decided_at ? formatBR(r.decided_at) : "";
          }
          toast("Alteração salva", "success");
        } catch (err) {
          toast("Falha ao salvar alteração: " + err.message, "danger");
        }
      }
    });
  });
  apply();
}

// Wrapper retro-compat — Relatorio Completo (botao verde no topo)
function _renderFullReport(data, chave, branch) {
  _renderReportInto(data, {
    summaryId: "frSummary",
    countsId:  "frCounts",
    contentId: "frContent",
    docKey: chave, branch: branch,   // v2.24 — contexto p/ salvar decisoes por campo
  });
}

// Sprint 18 + 19 — abre o modal de detalhes da nota a partir da tabela mestre
let _currentDocAck = null;   // {chave, branch} ativos no modal — usado pelo ack

async function openDocumentDetailsModal(chave, branch) {
  // Reusa o toggle global "Exibir apenas divergencias"
  const onlyErrors = $("fOnlyErrors") ? $("fOnlyErrors").checked : true;
  _currentDocAck = { chave, branch };

  const modalEl = document.getElementById("modalDocumentDetails");
  const modal = bootstrap.Modal.getOrCreateInstance(modalEl);

  // Loading state
  $("mddSummary").classList.add("d-none");
  $("mddSummary").innerHTML = "";
  $("mddCounts").innerHTML = "";
  $("mddContent").innerHTML = `
    <div class="text-center p-4">
      <div class="spinner-border text-primary" role="status"></div>
      <div class="mt-2 small text-muted">Carregando relatório do documento…</div>
    </div>`;
  // Sprint 19: limpa input de justificativa + esconde bloco ate dados chegarem
  if ($("mddAckNote")) $("mddAckNote").value = "";
  if ($("mddAckBlock")) $("mddAckBlock").classList.add("d-none");
  // v2.20: limpa estado da revisao manual
  if ($("mddReviewNote")) $("mddReviewNote").value = "";
  if ($("mddReviewStatus")) $("mddReviewStatus").innerHTML = "";
  modal.show();

  try {
    const data = await api(
      `/api/fiscal/document-audit?branch=${encodeURIComponent(branch)}` +
      `&chave=${encodeURIComponent(chave)}` +
      `&only_errors=${onlyErrors ? "true" : "false"}`,
    );
    _renderReportInto(data, {
      summaryId: "mddSummary",
      countsId:  "mddCounts",
      contentId: "mddContent",
      docKey: chave, branch: branch,   // v2.24 — contexto p/ salvar decisoes por campo
    });
    // v2.20: estado da revisao manual (visivel a todos)
    _renderReviewStatus(data && data.review);
    // v2.27 — "Revisão Manual" e "Tomada de Decisão" ocultos por ora (a pedido);
    // a decisão por campo já persiste. Mantidos no DOM para reativar facil depois.
    if ($("mddAckBlock")) $("mddAckBlock").classList.add("d-none");
  } catch (err) {
    $("mddContent").innerHTML = `<div class="alert alert-danger small">${err.message}</div>`;
  }
}

// Sprint 19 — handler do botao "Marcar como ciente" do modal de detalhes
$("mddBtnAck")?.addEventListener("click", async () => {
  if (!_currentDocAck || !_currentDocAck.chave) {
    return toast("Nenhum documento selecionado", "warning");
  }
  const btn = $("mddBtnAck");
  const note = ($("mddAckNote")?.value || "").trim();
  await withSpinner(btn, async () => {
    try {
      const r = await api("/api/fiscal/document/ack", {
        method: "POST",
        body: {
          doc_key: _currentDocAck.chave,
          branch:  _currentDocAck.branch,
          note:    note || null,
        },
      });
      toast(r.detail || "Documento marcado como ciente", "success");
      // Fecha o modal e recarrega a lista mestre
      const modalEl = document.getElementById("modalDocumentDetails");
      bootstrap.Modal.getInstance(modalEl)?.hide();
      _currentDocAck = null;
      await loadKpis();
      await loadAnomalies();
    } catch (err) {
      toast("Falha ao marcar ciente: " + err.message, "danger");
    }
  }, "Registrando…");
});

// v2.20 — estado da revisao manual no modal de detalhes (visivel a todos)
function _renderReviewStatus(review) {
  const el = $("mddReviewStatus");
  const btn = $("mddBtnReview");
  if (!el) return;
  if (review && review.reviewed_by) {
    el.innerHTML =
      `<span class="badge bg-info text-dark">✎ Revisado manualmente por ` +
      `${review.reviewed_by} em ${formatBR(review.reviewed_at)}</span>` +
      (review.note ? ` <span class="text-muted">— ${review.note}</span>` : "");
    if (btn && btn.querySelector(".label")) btn.querySelector(".label").textContent = "✎ Atualizar revisão";
  } else {
    el.innerHTML = `<span class="text-muted">Documento ainda não revisado manualmente.</span>`;
    if (btn && btn.querySelector(".label")) btn.querySelector(".label").textContent = "✎ Marcar como revisado";
  }
}

// v2.20 — handler do botao "Marcar como revisado"
$("mddBtnReview")?.addEventListener("click", async () => {
  if (!_currentDocAck || !_currentDocAck.chave) {
    return toast("Nenhum documento selecionado", "warning");
  }
  const btn = $("mddBtnReview");
  const note = ($("mddReviewNote")?.value || "").trim();
  await withSpinner(btn, async () => {
    try {
      const r = await api("/api/fiscal/document/review", {
        method: "POST",
        body: {
          doc_key: _currentDocAck.chave,
          branch:  _currentDocAck.branch,
          note:    note || null,
        },
      });
      toast(r.detail || "Documento revisado", "success");
      _renderReviewStatus({ reviewed_by: r.reviewed_by, reviewed_at: r.reviewed_at, note: r.note });
      await loadAnomalies();   // atualiza o selo "revisado" na lista mestre
    } catch (err) {
      toast("Falha ao registrar revisão: " + err.message, "danger");
    }
  }, "Registrando…");
});

// v2.24 — painel separado das decisoes manuais (o que foi alterado x sistema)
const _fkPretty = (fk) => {
  if (!fk) return "";
  const i = fk.indexOf(":");
  if (i < 0) return fk;
  const a = fk.slice(0, i), f = fk.slice(i + 1);
  return a === "H" ? `Cabeçalho · ${f}` : `Item ${a} · ${f}`;
};
const _MD_BADGE = {
  ok: '<span class="badge bg-success">✓ Conforme</span>',
  divergent: '<span class="badge bg-danger">✗ Divergente</span>',
  skipped: '<span class="badge bg-secondary">— Sem dado</span>',
};
// v2.28 — histórico de auditorias por decêndio
const _ENGINE_LABEL = {
  internal: "Interno (NF × XML)",
  financeiro_se2: "Financeiro (SF1 × SE2)",
  comercial_sc5_se1: "Comercial (SC5 × SE1)",
};
$("btnAuditRuns")?.addEventListener("click", async () => {
  const modal = bootstrap.Modal.getOrCreateInstance(document.getElementById("modalAuditRuns"));
  $("auditRunsRows").innerHTML = `<tr><td colspan="9" class="p-3 text-muted">Carregando…</td></tr>`;
  modal.show();
  try {
    const d = await api("/api/fiscal/audit-runs");
    const runs = d.runs || [];
    if (!runs.length) {
      $("auditRunsRows").innerHTML = `<tr><td colspan="9" class="p-3 text-muted">Nenhuma auditoria registrada ainda.</td></tr>`;
      return;
    }
    const ST = {
      done: '<span class="badge bg-success">concluída</span>',
      running: '<span class="badge bg-warning text-dark">em execução</span>',
      queued: '<span class="badge bg-secondary">na fila</span>',
      failed: '<span class="badge bg-danger">falhou</span>',
      canceled: '<span class="badge bg-secondary">cancelada</span>',
    };
    $("auditRunsRows").innerHTML = runs.map(r => {
      const per = (r.date_from && r.date_to) ? `${_fmtISOdate(r.date_from)} – ${_fmtISOdate(r.date_to)}` : "—";
      return `<tr>
        <td><strong>${per}</strong></td>
        <td>${(r.branches || []).join(", ") || "—"}</td>
        <td><small>${_ENGINE_LABEL[r.engine] || r.engine}</small></td>
        <td><small>${formatBR(r.created_at)}</small></td>
        <td>${r.docs}</td>
        <td>${r.divergencias}</td>
        <td>${ST[r.status] || r.status}</td>
        <td><small>${r.owner || "—"}</small></td>
        <td><button class="btn btn-sm btn-primary" data-run="${r.job_id}" data-label="${per.replace(/"/g, '&quot;')}">Ver documentos</button></td>
      </tr>`;
    }).join("");
    $("auditRunsRows").querySelectorAll("[data-run]").forEach(b =>
      b.addEventListener("click", () => {
        _jobFilter = { jobId: b.dataset.run, label: b.dataset.label };
        bootstrap.Modal.getInstance(document.getElementById("modalAuditRuns"))?.hide();
        loadAnomalies();
      }));
  } catch (err) {
    $("auditRunsRows").innerHTML = `<tr><td colspan="9" class="text-danger p-3">${err.message}</td></tr>`;
  }
});

$("btnManualDecisions")?.addEventListener("click", async () => {
  const modal = bootstrap.Modal.getOrCreateInstance(document.getElementById("modalManualDecisions"));
  $("mdKpis").innerHTML = `<div class="text-muted small p-2">Carregando…</div>`;
  $("mdByUser").innerHTML = "";
  $("mdRecent").innerHTML = "";
  modal.show();
  try {
    const params = new URLSearchParams();
    if ($("fFrom").value) params.set("date_from", $("fFrom").value);
    if ($("fTo").value)   params.set("date_to",   $("fTo").value);
    if ($("fBranch").value) params.set("branch",  $("fBranch").value);
    const d = await api("/api/fiscal/decisions-summary?" + params.toString());
    const bs = d.by_status || {};
    $("mdKpis").innerHTML = `
      <div class="kpi-card"><div class="label">Campos alterados</div><div class="value">${d.total || 0}</div></div>
      <div class="kpi-card"><div class="label">→ Conforme</div><div class="value text-success">${bs.ok || 0}</div></div>
      <div class="kpi-card"><div class="label">→ Divergente</div><div class="value text-danger">${bs.divergent || 0}</div></div>
      <div class="kpi-card"><div class="label">→ Sem dado</div><div class="value">${bs.skipped || 0}</div></div>
      <div class="kpi-card"><div class="label">Documentos afetados</div><div class="value">${d.docs || 0}</div></div>`;
    $("mdByUser").innerHTML = (d.by_user || []).length
      ? d.by_user.map(u => `<span class="badge bg-info text-dark me-1 mb-1">${u.user}: ${u.count}</span>`).join("")
      : `<span class="text-muted small">Nenhuma alteração manual no período.</span>`;
    $("mdRecent").innerHTML = (d.recent || []).length
      ? d.recent.map(r => {
          const dk = r.doc_key || "";
          const dkShort = dk.length >= 8 ? `…${dk.slice(-8)}` : (dk || "—");
          return `<tr>
            <td><small>${formatBR(r.decided_at)}</small></td>
            <td>${r.branch || "-"}</td>
            <td><code title="${dk}">${dkShort}</code></td>
            <td><small>${_fkPretty(r.field_key)}</small></td>
            <td>${_MD_BADGE[r.status] || r.status}</td>
            <td><small>${r.decided_by || "—"}</small></td>
          </tr>`;
        }).join("")
      : `<tr><td colspan="6" class="text-muted p-3">Nenhuma alteração manual registrada.</td></tr>`;
  } catch (err) {
    $("mdKpis").innerHTML = `<div class="alert alert-danger small">${err.message}</div>`;
  }
});

$("btnFullReport").addEventListener("click", async () => {
  await _populateFullReportBranches();
  $("frChave").value = "";
  $("frSummary").classList.add("d-none");
  $("frSummary").innerHTML = "";
  $("frCounts").innerHTML = "";
  $("frContent").innerHTML = `
    <div class="text-muted small p-3">
      Informe a chave NFe e clique em <strong>Auditar</strong> para gerar
      o relatório completo. <strong>Todos</strong> os campos serão exibidos
      (Conforme em verde, Divergente em vermelho/amarelo, Sem dado em cinza).
    </div>`;
  fullReportModal.show();
});

$("fullReportForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const branch = $("frBranch").value;
  const chave = ($("frChave").value || "").trim();
  if (chave.length !== 44 || !/^\d+$/.test(chave)) {
    return toast("Chave deve ter 44 dígitos numéricos", "warning");
  }
  // Sprint 17 — repassa o toggle do modal para o backend
  const onlyErrors = $("frOnlyErrors") ? $("frOnlyErrors").checked : true;
  const btn = e.target.querySelector("button[type=submit]");
  await withSpinner(btn, async () => {
    $("frContent").innerHTML = `<div class="text-muted small p-3">Carregando relatório…</div>`;
    try {
      const data = await api(
        `/api/fiscal/document-audit?branch=${encodeURIComponent(branch)}` +
        `&chave=${encodeURIComponent(chave)}` +
        `&only_errors=${onlyErrors ? "true" : "false"}`,
      );
      _renderFullReport(data, chave, branch);
    } catch (err) {
      $("frContent").innerHTML = `<div class="alert alert-danger small">${err.message}</div>`;
    }
  }, "Auditando…");
});


// ---- Tracker de progresso de auditoria fiscal ----------------------------
function _trackAuditJob(jobId) {
  const html = `
    <div class="modal fade" id="auditModal_${jobId}" tabindex="-1" data-bs-backdrop="static">
      <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content">
          <div class="modal-header">
            <h5 class="modal-title">Auditando documentos…</h5>
          </div>
          <div class="modal-body">
            <p class="text-muted small mb-2">Job: <code>${jobId.slice(0,8)}</code></p>
            <div class="progress" style="height:18px">
              <div id="ap_${jobId}" class="progress-bar progress-bar-striped progress-bar-animated"
                   role="progressbar" style="width:0%">0%</div>
            </div>
            <div id="am_${jobId}" class="text-muted small mt-2">Aguardando worker…</div>
            <div id="ae_${jobId}" class="text-warning small mt-1"></div>
          </div>
          <div class="modal-footer">
            <button class="btn btn-outline-danger" id="ac_${jobId}">Cancelar operação</button>
            <button class="btn btn-secondary" data-bs-dismiss="modal">Fechar (continua rodando)</button>
          </div>
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML("beforeend", html);
  const el = document.getElementById(`auditModal_${jobId}`);
  const modal = new bootstrap.Modal(el);
  modal.show();

  document.getElementById(`ac_${jobId}`).onclick = async () => {
    try {
      await api(`/api/reports/jobs/${jobId}`, { method: "DELETE" });
      toast("Cancelamento solicitado", "warning");
    } catch (e) { toast(e.message, "danger"); }
  };

  const startTs = Date.now();
  let delay = 3000;
  const MAX_DELAY = 15000;

  const poll = async () => {
    try {
      const j = await api(`/api/reports/jobs/${jobId}`);
      const bar = document.getElementById(`ap_${jobId}`);
      const msg = document.getElementById(`am_${jobId}`);
      if (!bar) return;

      const pct = Math.round(j.progress_pct || 0);
      bar.style.width = `${pct}%`;
      bar.textContent = `${pct}%`;

      const elapsedS = Math.round((Date.now() - startTs) / 1000);
      const processed = j.rows_processed || 0;
      let etaStr = "";
      if (processed > 0 && elapsedS > 5 && j.rows_total) {
        const rate = processed / elapsedS;
        const remaining = Math.max(0, j.rows_total - processed);
        const etaS = Math.round(remaining / Math.max(rate, 0.001));
        etaStr = ` · ETA ~${Math.ceil(etaS/60)}min`;
      }

      if (j.status === "queued") {
        msg.textContent = `Na fila — aguardando worker disponível (${elapsedS}s)`;
      } else if (j.status === "running") {
        msg.textContent = `Processando ${processed}/${j.rows_total ?? "?"} documentos${etaStr}`;
      } else if (j.status === "done") {
        bar.classList.remove("progress-bar-animated");
        msg.textContent = `Concluído em ${elapsedS}s — ${processed} doc(s) auditados.`;
        toast("Auditoria fiscal concluída — verifique anomalias na lista", "success");
        setTimeout(() => { try { modal.hide(); el.remove(); } catch {} loadAnomalies(); loadKpis(); }, 1200);
        return;
      } else if (j.status === "failed") {
        bar.classList.remove("progress-bar-animated");
        bar.classList.add("bg-danger");
        msg.textContent = `Falhou: [${j.error_code}] ${j.error_detail || ""}`;
        toast(`Auditoria falhou: ${j.error_code}`, "danger");
        return;
      } else if (j.status === "canceled") {
        bar.classList.remove("progress-bar-animated");
        msg.textContent = "Cancelado pelo usuário.";
        return;
      }
      delay = Math.min(MAX_DELAY, Math.floor(delay * 1.3));
      setTimeout(poll, delay);
    } catch (e) {
      setTimeout(poll, Math.min(MAX_DELAY, delay * 2));
    }
  };
  setTimeout(poll, delay);
}

$("fIncludeAcked").addEventListener("change", () => loadAnomalies());
$("fBranch")?.addEventListener("change", () => loadAnomalies());
// Sprint 17 — re-aplica filtro ao alternar "Exibir apenas divergências"
$("fOnlyErrors")?.addEventListener("change", () => loadAnomalies());
// v2.25 — filtro por cruzamento + situacao aplica na hora
$("fField")?.addEventListener("change", () => loadAnomalies());
$("fFieldStatus")?.addEventListener("change", () => { if ($("fField") && $("fField").value) loadAnomalies(); });

// Sprint 17 — toggle do modal Relatorio Completo: re-busca se ja ha chave preenchida
$("frOnlyErrors")?.addEventListener("change", () => {
  const chave = ($("frChave").value || "").trim();
  if (chave.length === 44 && /^\d+$/.test(chave)) {
    $("fullReportForm").dispatchEvent(new Event("submit", { cancelable: true }));
  }
});


// ============================================================
//  Modal de detalhes side-by-side + ack/snooze
// ============================================================

async function openDetailModal(anomalyId) {
  if (!$("anomalyDetailModal")) {
    document.body.insertAdjacentHTML("beforeend", `
      <div class="modal fade" id="anomalyDetailModal" tabindex="-1">
        <div class="modal-dialog modal-xl modal-dialog-centered">
          <div class="modal-content">
            <div class="modal-header">
              <h5 class="modal-title">🔍 Detalhe da anomalia <small id="adId" class="text-muted ms-2"></small></h5>
              <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
            </div>
            <div class="modal-body">
              <div id="adContent" class="small text-muted">Carregando…</div>
            </div>
            <div class="modal-footer flex-wrap gap-2" id="adActions"></div>
          </div>
        </div>
      </div>`);
  }
  const modal = new bootstrap.Modal("#anomalyDetailModal");
  $("adContent").innerHTML = `<div class="text-muted p-3">Carregando…</div>`;
  $("adActions").innerHTML = "";
  $("adId").textContent = `#${anomalyId}`;
  modal.show();

  try {
    const a = await api(`/api/fiscal/anomaly/${anomalyId}`);
    const isNcm = /ncm/i.test(a.field_compared || "");
    const isAusente = /nota_ausente/i.test(a.field_compared || "");
    const ackBanner = a.acknowledged_at
      ? `<div class="alert alert-secondary small mb-3">
           ✓ Marcada como ciente em <strong>${formatBR(a.acknowledged_at)}</strong>${
             a.ack_note ? ` — <em>"${a.ack_note}"</em>` : ""}
         </div>` : "";
    const snoozeBanner = (a.snoozed_until && new Date(a.snoozed_until) > new Date())
      ? `<div class="alert alert-info small mb-3">
           ⏰ Em snooze até <strong>${formatBR(a.snoozed_until)}</strong>${
             a.ack_note ? ` — <em>"${a.ack_note}"</em>` : ""}
         </div>` : "";
    let headerAlert = "";
    if (isAusente) {
      headerAlert = `<div class="alert alert-danger small">
        ⚠ <strong>Nota ausente:</strong> o XML foi importado no ERP (SDS) mas a NF
        ainda não foi classificada/lançada (sem SF1). Verifique imediatamente.
      </div>`;
    } else if (isNcm) {
      headerAlert = `<div class="alert alert-danger small">
        🚨 <strong>Compliance fiscal:</strong> divergência de NCM. Resolva ANTES do fechamento.
      </div>`;
    }

    $("adContent").innerHTML = `
      ${headerAlert}
      ${ackBanner}${snoozeBanner}

      <table class="table table-sm mb-3">
        <tr><th style="width:160px">Chave NFe</th><td><code>${a.doc_key}</code></td></tr>
        <tr><th>Filial</th><td>${a.branch}</td></tr>
        <tr><th>Fornecedor (cod/loja)</th><td><code>${a.supplier_cnpj || "—"}</code></td></tr>
        <tr><th>Campo comparado</th><td><strong>${a.field_compared}</strong></td></tr>
        <tr><th>Severidade</th><td>${SEV_BADGE[a.severity] || a.severity}</td></tr>
        <tr><th>Auditado em</th><td>${formatBR(a.audited_at)}</td></tr>
        <tr><th>Tolerância</th><td><small>${_tolerancesByField()[Object.keys(_tolerancesByField()).find(k => (a.field_compared||"").toLowerCase().includes(k)) || ""] || "—"}</small></td></tr>
      </table>

      <h6 class="text-uppercase text-muted small mb-2">Comparação lado a lado</h6>
      <div class="row g-3">
        <div class="col-md-6">
          <div class="anomaly-side anomaly-side-protheus">
            <div class="anomaly-side-title">📦 Protheus (SF1/SD1)</div>
            <pre class="anomaly-value">${(a.protheus_value || "(vazio)").replace(/</g,"&lt;")}</pre>
          </div>
        </div>
        <div class="col-md-6">
          <div class="anomaly-side anomaly-side-xml">
            <div class="anomaly-side-title">📄 XML internalizado (SDS/SDT)</div>
            <pre class="anomaly-value">${(a.xml_value || "(vazio)").replace(/</g,"&lt;")}</pre>
          </div>
        </div>
      </div>
    `;

    const isOpen = !a.acknowledged_at &&
      !(a.snoozed_until && new Date(a.snoozed_until) > new Date());
    if (isOpen) {
      $("adActions").innerHTML = `
        <div class="me-auto">
          <label class="form-label small mb-1">Nota (opcional)</label>
          <input id="adNote" class="form-control form-control-sm" style="width:260px"
                 placeholder="ex: contestado com o fornecedor">
        </div>
        <div class="d-flex gap-2">
          <div class="btn-group">
            <button class="btn btn-outline-info dropdown-toggle" data-bs-toggle="dropdown">
              ⏰ Snooze
            </button>
            <ul class="dropdown-menu dropdown-menu-end">
              <li><a class="dropdown-item snooze-opt" href="#" data-days="1">1 dia</a></li>
              <li><a class="dropdown-item snooze-opt" href="#" data-days="7">7 dias</a></li>
              <li><a class="dropdown-item snooze-opt" href="#" data-days="30">30 dias</a></li>
            </ul>
          </div>
          <button class="btn btn-success" id="btnAck">✓ Marcar como ciente</button>
          <button class="btn btn-secondary" data-bs-dismiss="modal">Fechar</button>
        </div>`;
      $("btnAck").addEventListener("click", () => doAck(anomalyId, modal, null));
      document.querySelectorAll(".snooze-opt").forEach(a =>
        a.addEventListener("click", e => {
          e.preventDefault();
          doAck(anomalyId, modal, parseInt(a.dataset.days, 10));
        }));
    } else {
      $("adActions").innerHTML = `
        <button class="btn btn-outline-warning me-auto" id="btnUnack">
          ↶ Reabrir (remover ciência/snooze)
        </button>
        <button class="btn btn-secondary" data-bs-dismiss="modal">Fechar</button>`;
      $("btnUnack").addEventListener("click", async () => {
        try {
          await api(`/api/fiscal/anomaly/${anomalyId}/unack`, { method: "POST" });
          toast("Anomalia reaberta", "warning");
          modal.hide();
          await loadKpis(); await loadAnomalies();
        } catch (e) { toast(e.message, "danger"); }
      });
    }
  } catch (e) {
    $("adContent").innerHTML = `<div class="alert alert-danger">${e.message}</div>`;
  }
}

async function doAck(anomalyId, modal, snoozeDays) {
  const note = $("adNote")?.value?.trim() || null;
  const body = {};
  if (note) body.note = note;
  if (snoozeDays) body.snooze_days = snoozeDays;
  try {
    await api(`/api/fiscal/anomaly/${anomalyId}/ack`, { method: "POST", body });
    toast(snoozeDays
      ? `Em snooze por ${snoozeDays} dia(s)`
      : "Marcada como ciente — sai do Dashboard", "success");
    modal.hide();
    await loadKpis(); await loadAnomalies();
  } catch (e) { toast(e.message, "danger"); }
}


// ============================================================
//  Export CSV / XLSX
// ============================================================
document.querySelectorAll(".exp-anomaly").forEach(el => {
  el.addEventListener("click", async (e) => {
    e.preventDefault();
    const fmt = el.dataset.fmt;
    const params = new URLSearchParams();
    if ($("fFrom").value) params.set("date_from", $("fFrom").value);
    if ($("fTo").value)   params.set("date_to",   $("fTo").value);
    if ($("fSev").value)  params.set("severity",  $("fSev").value);
    if ($("fBranch").value) params.set("branch",  $("fBranch").value);
    if ($("fIncludeAcked").checked) params.set("include_acked", "true");
    // Sprint 17 — exporta respeitando o toggle de divergencias
    const onlyErrorsExp = $("fOnlyErrors") ? $("fOnlyErrors").checked : true;
    params.set("only_errors", onlyErrorsExp ? "true" : "false");
    params.set("fmt", fmt);
    try {
      const blob = await api("/api/fiscal/anomalies/export?" + params.toString());
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `anomalias_${new Date().toISOString().slice(0,10)}.${fmt}`;
      link.click();
      URL.revokeObjectURL(url);
      toast(`Lista exportada em ${fmt.toUpperCase()}`, "success");
    } catch (err) { toast("Falha na exportação: " + err.message, "danger"); }
  });
});


(async () => {
  await loadEngineInfo();
  await _populateBranchFilter();   // default Filial 01
  await _loadAnomalyFields();      // v2.25 — cruzamentos disponiveis no filtro
  await loadKpis();
  await loadAnomalies();
})();

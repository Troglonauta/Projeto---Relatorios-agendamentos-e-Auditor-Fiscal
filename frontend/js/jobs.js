/* Helper de fila — polling com backoff exponencial.
   Usado pelo Builder Visual quando o relatorio passa do limite in-memory.
*/
import { api, toast } from "./api.js";

/**
 * Enfileira um job a partir do payload do Builder e acompanha o progresso
 * com um modal Bootstrap. Resolve com o blob baixado, ou rejeita em erro/cancel.
 */
export async function runReportJob(payload, { format = "xlsx" } = {}) {
  const { job_id } = await api(`/api/reports/jobs?file_format=${format}`,
                                { method: "POST", body: payload });

  const modal = _openModal(job_id);
  let delay = 2000;
  const MAX_DELAY = 15000;

  try {
    while (true) {
      await _sleep(delay);
      const j = await api(`/api/reports/jobs/${job_id}`);
      _renderProgress(modal, j);

      if (j.status === "done") {
        const blob = await api(`/api/reports/jobs/${job_id}/download`);
        _closeModal(modal);
        _downloadBlob(blob, `relatorio_${job_id.slice(0,8)}.${format}`);
        toast(`Relatorio pronto (${j.rows_processed || 0} linhas)`, "success");
        return j;
      }
      if (j.status === "failed") {
        _closeModal(modal);
        throw new Error(`[${j.error_code}] ${j.error_detail}`);
      }
      if (j.status === "canceled") {
        _closeModal(modal);
        toast("Job cancelado", "warning");
        return j;
      }
      delay = Math.min(MAX_DELAY, Math.floor(delay * 1.5));
    }
  } catch (e) {
    _closeModal(modal);
    throw e;
  }
}

// ---- UI ---------------------------------------------------------------------

function _openModal(jobId) {
  const html = `
    <div class="modal fade" id="jobModal_${jobId}" tabindex="-1" data-bs-backdrop="static">
      <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content">
          <div class="modal-header">
            <h5 class="modal-title">Gerando relatorio...</h5>
          </div>
          <div class="modal-body">
            <p class="text-muted small mb-2">Job ID: <code>${jobId.slice(0,8)}</code></p>
            <div class="progress" style="height:18px">
              <div id="jp_${jobId}" class="progress-bar progress-bar-striped progress-bar-animated"
                   role="progressbar" style="width:0%">0%</div>
            </div>
            <div id="jm_${jobId}" class="text-muted small mt-2">Aguardando worker…</div>
          </div>
          <div class="modal-footer">
            <button class="btn btn-outline-danger" id="jc_${jobId}">Cancelar</button>
          </div>
        </div>
      </div>
    </div>`;
  document.body.insertAdjacentHTML("beforeend", html);
  const el = document.getElementById(`jobModal_${jobId}`);
  const modal = new bootstrap.Modal(el);
  modal.show();
  document.getElementById(`jc_${jobId}`).onclick = async () => {
    try { await api(`/api/reports/jobs/${jobId}`, { method: "DELETE" }); }
    catch (e) { toast(e.message, "danger"); }
  };
  return { el, modal, jobId };
}

function _renderProgress({ jobId }, job) {
  const bar = document.getElementById(`jp_${jobId}`);
  const msg = document.getElementById(`jm_${jobId}`);
  if (!bar) return;
  const pct = Math.round(job.progress_pct || 0);
  bar.style.width = `${pct}%`;
  bar.textContent = `${pct}%`;
  if (msg) {
    if (job.status === "queued") msg.textContent = "Na fila — aguardando worker disponivel…";
    else if (job.status === "running") msg.textContent =
      `Processando ${job.rows_processed || 0}/${job.rows_total || "?"} linhas`;
  }
}

function _closeModal({ el, modal }) {
  try { modal.hide(); } catch {}
  setTimeout(() => el?.remove(), 400);
}

function _sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function _downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url; link.download = filename; link.click();
  URL.revokeObjectURL(url);
}

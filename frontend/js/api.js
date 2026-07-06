/* Cliente HTTP único — JWT, redirecionamento em 401, idle-logout, spinners. */

// Sprint 8 Part 1 — aplica tema dark/light antes do render (evita flash branco).
// Importado por TODAS as paginas via cadeia api.js.
import { applyThemeFromStorage } from "./theme.js";
applyThemeFromStorage();

// Hotfix v2.0 — botao "olho" universal em todo input[type=password].
import "./pwd-toggle.js";

const API_BASE = "";
const TOKEN_KEY    = "pr_token";
const USER_KEY     = "pr_user";
const ACTIVITY_KEY = "pr_last_activity";   // timestamp ms da última interação
const SETTINGS_KEY = "pr_settings";        // cache do /api/settings/public

const TZ = "America/Sao_Paulo";

export const auth = {
  // Sessao guardada em sessionStorage (nao localStorage): e' apagada ao FECHAR a
  // aba/janela -> usuario deslogado. Sobrevive a refresh e navegacao na mesma aba.
  get token()  { return sessionStorage.getItem(TOKEN_KEY); },
  set token(v) { v ? sessionStorage.setItem(TOKEN_KEY, v) : sessionStorage.removeItem(TOKEN_KEY); },
  get user()   { try { return JSON.parse(sessionStorage.getItem(USER_KEY) || "null"); } catch { return null; } },
  set user(v)  { v ? sessionStorage.setItem(USER_KEY, JSON.stringify(v)) : sessionStorage.removeItem(USER_KEY); },
  clear() {
    this.token = null; this.user = null;
    sessionStorage.removeItem(ACTIVITY_KEY);
    localStorage.removeItem(ACTIVITY_KEY);   // limpa tambem chave legada em localStorage
  },
  isAdmin() { return this.user?.role === "admin"; },
  hasAction(a) {
    const u = this.user;
    if (!u) return false;
    if (u.role === "admin") return true;
    return (u.allowed_actions || []).includes(a);
  },
  /** Marca atividade do usuário (mouse/teclado/clique). Reinicia o relógio do idle. */
  touchActivity() { sessionStorage.setItem(ACTIVITY_KEY, Date.now().toString()); },
  lastActivity()  { return parseInt(sessionStorage.getItem(ACTIVITY_KEY) || "0", 10); },
};

/* === Settings públicos (timeout de idle vem daqui) ============= */
export async function loadPublicSettings(forceRefresh = false) {
  if (!forceRefresh) {
    const cached = localStorage.getItem(SETTINGS_KEY);
    if (cached) { try { return JSON.parse(cached); } catch {} }
  }
  try {
    const s = await fetch(`${API_BASE}/api/settings/public`).then(r => r.json());
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(s));
    return s;
  } catch {
    return { idle_minutes: 20, timezone: TZ };
  }
}

/* === fetch wrapper ============================================ */
export async function api(path, { method = "GET", body, form, headers = {} } = {}) {
  const opts = { method, headers: { ...headers } };
  if (auth.token) opts.headers["Authorization"] = `Bearer ${auth.token}`;
  if (form) {
    opts.body = form;
  } else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }

  const res = await fetch(`${API_BASE}${path}`, opts);
  if (res.status === 401) {
    auth.clear();
    if (!location.pathname.endsWith("login.html")) {
      location.href = "/static/pages/login.html?reason=expired";
    }
    throw new Error("Não autenticado");
  }

  const ct = res.headers.get("content-type") || "";
  if (!res.ok) {
    let detail = res.statusText;
    if (ct.includes("application/json")) {
      try { detail = (await res.json()).detail || detail; } catch {}
    }
    throw new Error(detail);
  }
  if (ct.includes("application/json")) return await res.json();
  return await res.blob();
}

/* === Toasts =================================================== */
export function toast(msg, kind = "info") {
  const map = { info: "primary", success: "success", danger: "danger", warning: "warning" };
  const holder = document.getElementById("toastHolder") || (() => {
    const d = document.createElement("div"); d.id = "toastHolder"; document.body.appendChild(d); return d;
  })();
  const el = document.createElement("div");
  el.className = `alert alert-${map[kind] || "primary"} shadow-sm`;
  el.style.minWidth = "260px";
  el.textContent = msg;
  holder.appendChild(el);
  setTimeout(() => el.remove(), 4500);
}

/* === Auth helpers ============================================= */
export function requireAuth() {
  if (!auth.token) { location.href = "/static/pages/login.html"; return false; }
  return true;
}

/** Landing page apropriada para o papel do usuario corrente.
 *  Admin -> Dashboard. Operador -> Consultas Protheus.
 *  Usado pelo login e por qualquer redirect generico.
 */
export function landingPage() {
  return auth.isAdmin() ? "dashboard.html" : "protheus.html";
}

export async function logout(reason) {
  /* Tenta liberar o slot de sessao concorrente no backend (Fase 4).
     Falha silenciosa: mesmo se o backend nao responder, o front limpa o token.
  */
  if (auth.token && reason !== "expired" && reason !== "idle") {
    try {
      await fetch("/api/auth/logout", {
        method: "POST",
        headers: { "Authorization": `Bearer ${auth.token}` },
      });
    } catch { /* ignora — pode estar offline */ }
  }
  auth.clear();
  const url = reason ? `/static/pages/login.html?reason=${encodeURIComponent(reason)}` : "/static/pages/login.html";
  location.href = url;
}

/* === Spinner helper para botões ================================
 *  withSpinner(btn, async () => { ... }, "Carregando…")
 *  - desabilita o botão, troca o texto e mostra um spinner Bootstrap.
 *  - restaura ao final, mesmo se a promise rejeitar.
 * ============================================================ */
export async function withSpinner(btn, fn, busyLabel = "Carregando…") {
  if (!btn) return await fn();
  const labelEl = btn.querySelector(".label") || btn;
  const spinEl  = btn.querySelector(".spinner-border");
  const original = labelEl.textContent;
  btn.disabled = true;
  labelEl.textContent = busyLabel;
  if (spinEl) spinEl.classList.remove("d-none");
  try {
    return await fn();
  } finally {
    btn.disabled = false;
    labelEl.textContent = original;
    if (spinEl) spinEl.classList.add("d-none");
  }
}

/* === Formatação de datas no fuso de Brasília =================== */
export function formatBR(ts, opts = {}) {
  if (!ts) return "-";
  const d = (ts instanceof Date) ? ts : new Date(ts);
  return d.toLocaleString("pt-BR", { timeZone: TZ, ...opts });
}

/* === Idle watcher (auto-logout por inatividade) ================
 *  Inicia automaticamente em qualquer página autenticada que importar layout.js.
 *  Aviso visual aos 30s do timeout; logout no momento do timeout.
 * ============================================================ */
let idleTimerStarted = false;
let idleSettings = null;

export async function startIdleWatcher() {
  if (idleTimerStarted) return;
  idleTimerStarted = true;
  idleSettings = await loadPublicSettings();
  const idleMs = (idleSettings.idle_minutes || 20) * 60 * 1000;
  if (!auth.lastActivity()) auth.touchActivity();

  const events = ["mousemove", "mousedown", "keydown", "scroll", "touchstart"];
  events.forEach(e => window.addEventListener(e, () => auth.touchActivity(), { passive: true }));

  // Heartbeat — "sinal de vida" da aba aberta para o limite de sessoes
  // concorrentes. Quando a aba fecha, para de bater e o servidor libera o
  // slot em ate SESSION_LIVENESS_SECONDS (a app e' multi-pagina, entao NAO
  // dava p/ revogar no fechamento — isso deslogaria a cada troca de menu).
  const HEARTBEAT_MS = 60_000;
  async function _beat() {
    if (!auth.token) return;
    try {
      await fetch(`${API_BASE}/api/auth/heartbeat`, {
        method: "POST",
        headers: { "Authorization": `Bearer ${auth.token}` },
        keepalive: true,
      });
    } catch { /* sem rede — proxima batida resolve */ }
  }
  _beat();
  setInterval(_beat, HEARTBEAT_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") _beat();
  });

  // Banner reutilizável de aviso
  let banner = null;
  function ensureBanner() {
    if (banner) return banner;
    banner = document.createElement("div");
    banner.className = "session-banner";
    banner.hidden = true;
    document.body.appendChild(banner);
    return banner;
  }

  setInterval(() => {
    if (!auth.token) return;
    const elapsed = Date.now() - auth.lastActivity();
    const remain = idleMs - elapsed;
    const b = ensureBanner();
    if (remain <= 0) {
      b.hidden = true;
      logout("idle");
    } else if (remain <= 30_000) {
      b.hidden = false;
      const sec = Math.max(1, Math.ceil(remain / 1000));
      b.textContent = `Sessão expira em ${sec}s por inatividade — mexa o mouse para continuar.`;
    } else if (!b.hidden) {
      b.hidden = true;
    }
  }, 5000);
}

/* Renderiza sidebar + topbar, gerencia menu ativo, arranca idle-watcher
   e aplica branding (cor primaria + logo) lido de /api/settings/public. */
import { auth, logout, requireAuth, startIdleWatcher, loadPublicSettings } from "./api.js";
import { getTheme, toggleTheme } from "./theme.js";

// ---- Helper: aplica cor primaria escolhida no Wizard ----------------------
function _darkenHex(hex, amount = 0.20) {
  // Reduz a luminosidade em N%. Suporta #RGB e #RRGGBB. Fallback: retorna hex original.
  try {
    let h = hex.replace("#", "");
    if (h.length === 3) h = h.split("").map(c => c + c).join("");
    const r = parseInt(h.slice(0,2),16), g = parseInt(h.slice(2,4),16), b = parseInt(h.slice(4,6),16);
    const f = 1 - amount;
    const r2 = Math.max(0, Math.round(r * f));
    const g2 = Math.max(0, Math.round(g * f));
    const b2 = Math.max(0, Math.round(b * f));
    return "#" + [r2, g2, b2].map(v => v.toString(16).padStart(2, "0")).join("");
  } catch { return hex; }
}

function _softenHex(hex, amount = 0.85) {
  // Mistura com branco. Util para tons de fundo "soft".
  try {
    let h = hex.replace("#", "");
    if (h.length === 3) h = h.split("").map(c => c + c).join("");
    const r = parseInt(h.slice(0,2),16), g = parseInt(h.slice(2,4),16), b = parseInt(h.slice(4,6),16);
    const mix = (c) => Math.round(c + (255 - c) * amount);
    return "#" + [mix(r), mix(g), mix(b)].map(v => v.toString(16).padStart(2, "0")).join("");
  } catch { return hex; }
}

let _brandingApplied = false;
async function applyBranding() {
  if (_brandingApplied) return;
  _brandingApplied = true;
  try {
    const cfg = await loadPublicSettings();
    const color = cfg.branding?.primary_color || "#2E8B3D";
    const dark = _darkenHex(color, 0.22);
    const soft = _softenHex(color, 0.88);
    const menuDark = _darkenHex(color, 0.45);

    const styleEl = document.createElement("style");
    styleEl.id = "fx-branding-override";
    styleEl.textContent = `
      :root {
        --fx-primary: ${color};
        --fx-primary-dark: ${dark};
        --fx-primary-soft: ${soft};
        --fx-menu: ${dark};
        --fx-menu-dark: ${menuDark};
      }`;
    document.head.appendChild(styleEl);
  } catch {
    // Sem internet/settings — segue com defaults Fertimaxi.
  }
}

// ---- Layout principal ----------------------------------------------------
export function renderLayout({ active, title }) {
  if (!requireAuth()) return;
  const u = auth.user || {};

  // Hotfix2 v2.0 — marca o body com a role do usuario. CSS usa para
  // ocultar elementos da visao operador (versao/build/tz no rodape,
  // .form-text de ajuda em formularios, etc).
  document.body.classList.remove("role-admin", "role-operator");
  document.body.classList.add(u.role === "admin" ? "role-admin" : "role-operator");
  const nav = [
    // Fase 4: Dashboard agora e' exclusivo para admin — operador entra direto
    // em "Consultas Protheus" (a sua tela primaria de trabalho).
    { id: "dashboard", href: "dashboard.html", label: "Dashboard", admin: true },
    { id: "protheus",  href: "protheus.html",  label: "Consultas Protheus" },
    { id: "schedules", href: "schedules.html", label: "Agendamentos", action: "schedule" },
    { id: "fiscal",    href: "fiscal.html",    label: "Auditor Fiscal", action: "fiscal" },
    { id: "profiles",  href: "profiles.html",  label: "Perfis & Módulos", admin: true },
    { id: "users",     href: "users.html",     label: "Usuários", admin: true },
    { id: "audit",     href: "audit.html",     label: "Auditoria", admin: true },
    { id: "admin",     href: "admin.html",     label: "Administração", admin: true },
  ];

  const links = nav
    .filter(n => !n.admin || auth.isAdmin())
    .filter(n => !n.action || auth.hasAction(n.action))
    .map(n => `<a class="${n.id === active ? 'active' : ''}" href="${n.href}">${n.label}</a>`)
    .join("");

  document.body.innerHTML = `
    <div class="app-shell">
      <aside class="sidebar">
        <div class="brand">
          <img src="/api/branding/logo" alt="Logo"
               onerror="this.replaceWith(Object.assign(document.createElement('span'),{textContent:'FERTIMAXI', className:'brand-fallback'}))">
        </div>
        <nav class="nav">${links}</nav>
        <!-- Sprint 8 Part 1 — toggle de tema (sun/moon) -->
        <div class="theme-toggle-wrap">
          <button id="btnThemeToggle" class="theme-toggle-btn" type="button"
                  title="Alternar entre tema claro e escuro">
            <span class="theme-icon" id="themeIcon">🌙</span>
            <span class="theme-label" id="themeLabel">Modo escuro</span>
          </button>
        </div>
        <div class="footer-info" id="versionTag">
          <div class="version-line"><span class="vlabel">versão</span> <strong id="appVersion">—</strong></div>
          <div class="version-build"><span class="vlabel">build</span> <span id="appBuild">—</span></div>
          <div class="version-tz"><span class="vlabel">fuso</span> <span id="appTz">America/São_Paulo (BRT)</span></div>
        </div>
      </aside>
      <main class="main">
        <header class="topbar">
          <div class="page-title">${title}</div>
          <div class="d-flex align-items-center gap-2">
            <span class="topbar-clock" id="topbarClock">—</span>
            <span class="user-pill">
              <strong>${u.username || ""}</strong> · ${u.role || ""}
            </span>
            <button id="btnLogout" class="btn btn-sm btn-outline-secondary">Sair</button>
          </div>
        </header>
        <div class="content" id="page"></div>
      </main>
    </div>
    <div id="toastHolder"></div>
  `;
  document.getElementById("btnLogout").onclick = () => logout("manual");
  // Hotfix2 v2.0 — reaplica role class apos o innerHTML (defensivo).
  document.body.classList.add(u.role === "admin" ? "role-admin" : "role-operator");
  auth.touchActivity();
  startIdleWatcher();
  applyBranding();
  applyVersionAndClock();
  setupThemeToggle();
}

// Sprint 8 Part 1 — botao Lua/Sol no sidebar
function _renderThemeToggleLabel() {
  const isDark = getTheme() === "dark";
  const ic = document.getElementById("themeIcon");
  const lb = document.getElementById("themeLabel");
  if (ic) ic.textContent = isDark ? "☀️" : "🌙";
  if (lb) lb.textContent = isDark ? "Modo claro" : "Modo escuro";
}

function setupThemeToggle() {
  const btn = document.getElementById("btnThemeToggle");
  if (!btn) return;
  _renderThemeToggleLabel();
  btn.addEventListener("click", () => {
    toggleTheme();
    _renderThemeToggleLabel();
  });
}

// ---- Versao no rodape + relogio do topbar (Horario de Brasilia) ----------
async function applyVersionAndClock() {
  try {
    const cfg = await loadPublicSettings();
    const v   = cfg.version || "?";
    const bld = cfg.build_date || "—";
    const elV = document.getElementById("appVersion");
    const elB = document.getElementById("appBuild");
    if (elV) elV.textContent = `v${v}`;
    if (elB) {
      // formata YYYY-MM-DD -> DD/MM/YYYY
      const m = String(bld).match(/^(\d{4})-(\d{2})-(\d{2})$/);
      elB.textContent = m ? `${m[3]}/${m[2]}/${m[1]}` : bld;
    }
  } catch { /* segue com defaults */ }

  // Relogio em tempo real no topbar (fuso de Brasilia)
  const clockEl = document.getElementById("topbarClock");
  if (!clockEl) return;
  const tick = () => {
    const now = new Date();
    const opts = { timeZone: "America/Sao_Paulo", hour: "2-digit", minute: "2-digit", second: "2-digit" };
    clockEl.textContent = "🕐 " + now.toLocaleTimeString("pt-BR", opts) + " BRT";
  };
  tick();
  setInterval(tick, 1000);
}

// Favicon + branding tambem nas paginas sem layout (login, setup, change-password)
(function ensureFavicon() {
  if (!document.querySelector('link[rel="icon"]')) {
    const l = document.createElement("link");
    l.rel = "icon"; l.type = "image/png"; l.href = "/api/branding/logo";
    document.head.appendChild(l);
  }
  applyBranding();
})();

/* Sprint 8 Part 1 — Dark Mode toggle.
 *
 * - Aplica `.dark-mode` em <body> + `data-bs-theme="dark"` em <html>
 *   (esse atributo ativa o suporte dark nativo do Bootstrap 5.3+).
 * - Persiste em localStorage (`pr_theme`).
 * - `applyThemeFromStorage()` deve rodar o mais cedo possivel (importado
 *   no topo de api.js) para evitar flash branco antes do CSS aplicar.
 * - `toggleTheme()` alterna entre light/dark e dispara `theme:changed`
 *   no document — paginas que querem reagir (ex: redesenhar charts) podem
 *   ouvir o evento.
 */

const THEME_KEY = "pr_theme";

export function getTheme() {
  const v = (localStorage.getItem(THEME_KEY) || "").toLowerCase();
  return v === "dark" ? "dark" : "light";
}

function _applyToDom(theme) {
  // <html data-bs-theme=...> ativa o suporte dark nativo do Bootstrap 5.3
  // para componentes que NAO sao via variables (ex: dropdown, modal-content).
  document.documentElement.setAttribute("data-bs-theme", theme);
  if (document.body) {
    document.body.classList.toggle("dark-mode", theme === "dark");
  } else {
    // <body> ainda nao montado — aplica quando estiver disponivel.
    document.addEventListener("DOMContentLoaded", () => {
      document.body.classList.toggle("dark-mode", theme === "dark");
    }, { once: true });
  }
}

export function applyThemeFromStorage() {
  _applyToDom(getTheme());
}

export function setTheme(theme) {
  const t = theme === "dark" ? "dark" : "light";
  localStorage.setItem(THEME_KEY, t);
  _applyToDom(t);
  document.dispatchEvent(new CustomEvent("theme:changed", { detail: { theme: t } }));
}

export function toggleTheme() {
  setTheme(getTheme() === "dark" ? "light" : "dark");
}

// Auto-bootstrap quando o modulo for importado.
applyThemeFromStorage();

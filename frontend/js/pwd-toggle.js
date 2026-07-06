/* Hotfix v2.0 — botao "olho" universal para input[type=password].
 *
 * Edge/IE tem ::-ms-reveal nativo (CSS ja forca visibilidade).
 * Chrome/Firefox/Safari NAO tem — esse modulo injeta um botao manual
 * que troca type=password <-> type=text.
 *
 * Auto-attach: ao carregar, varre o DOM existente. MutationObserver
 * cuida de inputs criados dinamicamente (ex: modais Bootstrap).
 */

// Icones SVG profissionais (Bootstrap Icons: eye / eye-slash) — herdam a cor
// via fill="currentColor". (Sem emojis — visual profissional e consistente.)
const EYE =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">' +
  '<path d="M16 8s-3-5.5-8-5.5S0 8 0 8s3 5.5 8 5.5S16 8 16 8M1.173 8a13 13 0 0 1 1.66-2.043C4.12 4.668 5.88 3.5 8 3.5s3.879 1.168 5.168 2.457A13 13 0 0 1 14.828 8q-.086.13-.195.288c-.335.48-.83 1.12-1.465 1.755C11.879 11.332 10.119 12.5 8 12.5s-3.879-1.168-5.168-2.457A13 13 0 0 1 1.172 8z"/>' +
  '<path d="M8 5.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5M4.5 8a3.5 3.5 0 1 1 7 0 3.5 3.5 0 0 1-7 0"/></svg>';
const EYE_SLASH =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">' +
  '<path d="m10.79 12.912-1.614-1.615a3.5 3.5 0 0 1-4.474-4.474l-2.06-2.06C.938 6.278 0 8 0 8s3 5.5 8 5.5a7 7 0 0 0 2.79-.588M5.21 3.088A7 7 0 0 1 8 2.5c5 0 8 5.5 8 5.5s-.939 1.721-2.641 3.238l-2.062-2.062a3.5 3.5 0 0 0-4.474-4.474z"/>' +
  '<path d="M5.525 7.646a2.5 2.5 0 0 0 2.829 2.829zm4.95.708-2.829-2.83a2.5 2.5 0 0 1 2.829 2.829zm3.171 6-12-12 .708-.708 12 12z"/></svg>';

function _attach(input) {
  if (!input || input.dataset.pwdToggleAttached === "1") return;
  if (input.type !== "password") return;
  // Evita em inputs hidden ou que ja estao dentro de um wrapper especial
  if (input.closest(".pwd-wrap")) return;
  input.dataset.pwdToggleAttached = "1";

  // Cria um wrapper relative em volta do input (preserva layout do Bootstrap)
  const wrap = document.createElement("span");
  wrap.className = "pwd-wrap";
  // O wrapper assume o display do input pra nao quebrar grid/flex
  const computedDisplay = getComputedStyle(input).display;
  wrap.style.display = computedDisplay.includes("block") ? "block" : "inline-block";
  wrap.style.width = "100%";

  input.parentNode.insertBefore(wrap, input);
  wrap.appendChild(input);

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "pwd-toggle";
  btn.tabIndex = -1;        // nao quebra a navegacao por Tab do form
  btn.setAttribute("aria-label", "Mostrar/ocultar senha");
  btn.title = "Mostrar/ocultar senha";
  btn.innerHTML = EYE;
  btn.addEventListener("click", () => {
    if (input.type === "password") {
      input.type = "text";
      input.classList.add("pwd-revealed");
      btn.innerHTML = EYE_SLASH;
    } else {
      input.type = "password";
      input.classList.remove("pwd-revealed");
      btn.innerHTML = EYE;
    }
  });
  wrap.appendChild(btn);
}

function _scan(root = document) {
  root.querySelectorAll('input[type="password"]').forEach(_attach);
}

// Boot — roda ao DOMContentLoaded e tambem em inputs criados depois.
function _boot() {
  _scan();
  const observer = new MutationObserver(muts => {
    for (const m of muts) {
      for (const node of m.addedNodes) {
        if (node.nodeType !== 1) continue;
        if (node.tagName === "INPUT" && node.type === "password") {
          _attach(node);
        } else {
          _scan(node);
        }
      }
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", _boot, { once: true });
} else {
  _boot();
}

export {};   // marca como modulo ES

import { api, auth, withSpinner, landingPage } from "./api.js";

const alertBox = document.getElementById("alertBox");
function showAlert(msg, kind = "danger") {
  alertBox.innerHTML = `<div class="alert alert-${kind} py-2 small">${msg}</div>`;
}

// Mensagem contextual quando o usuário caiu aqui por timeout/expiração/revogação.
const reason = new URLSearchParams(location.search).get("reason");
if (reason === "idle") {
  showAlert("Sessão encerrada por inatividade. Faça login novamente.", "warning");
} else if (reason === "expired") {
  showAlert("Sua sessão expirou. Faça login novamente.", "warning");
} else if (reason === "revoked") {
  showAlert("Sessão encerrada — você foi desconectado por outra sessão ou pelo limite de acessos simultâneos.", "warning");
}

document.getElementById("loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  alertBox.innerHTML = "";
  const username = document.getElementById("username").value.trim();
  const password = document.getElementById("password").value;
  const btn = document.getElementById("btnLogin");

  await withSpinner(btn, async () => {
    try {
      const form = new URLSearchParams({ username, password });
      const data = await api("/api/auth/login", { method: "POST", form });
      auth.token = data.access_token;
      auth.user  = data.user;
      auth.touchActivity();   // marca início da sessão para o idle-watcher

      if (data.must_change_password) {
        location.href = "change-password.html?first=1";
      } else {
        location.href = landingPage();
      }
    } catch (err) {
      const msg = err.message || "Falha ao autenticar";
      // 429 = limite de sessoes concorrentes — destaque amarelo
      const isLimit = /limite.*acesso/i.test(msg) || /Too Many/i.test(msg);
      showAlert(msg, isLimit ? "warning" : "danger");
    }
  }, "Entrando…");
});

document.getElementById("forgotLink").addEventListener("click", (e) => {
  e.preventDefault();
  new bootstrap.Modal("#forgotModal").show();
});

document.getElementById("forgotForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const email = document.getElementById("forgotEmail").value.trim();
  try {
    await api("/api/auth/forgot-password", { method: "POST", body: { email } });
    bootstrap.Modal.getInstance("#forgotModal").hide();
    showAlert("Se o e-mail existir, uma senha temporária foi enviada.", "success");
  } catch (err) {
    showAlert(err.message || "Falha ao solicitar");
  }
});

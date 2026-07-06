/* Setup Wizard — fluxo passo a passo (Fase 3). Nao depende do api.js
   antigo porque ainda nao tem token. */

const STEPS = ["branding", "db", "smtp", "admin", "finish"];
let current = 0;
const data = {};

const $ = (id) => document.getElementById(id);
const alertBox = $("alertBox");

function show(name) {
  document.querySelectorAll(".setup-section").forEach(s => {
    s.hidden = s.dataset.section !== name;
  });
  document.querySelectorAll(".setup-steps li").forEach(li => {
    li.classList.remove("active");
    if (li.dataset.step === name) li.classList.add("active");
  });
  alertBox.innerHTML = "";
  if (name === "finish") renderReview();
}

function setDone(name) {
  const li = document.querySelector(`.setup-steps li[data-step="${name}"]`);
  if (li) li.classList.add("done");
}

function alertMsg(msg, kind = "danger") {
  alertBox.innerHTML = `<div class="alert alert-${kind} small">${msg}</div>`;
}

async function http(path, opts = {}) {
  const init = { method: opts.method || "POST", headers: {} };
  if (opts.form) {
    init.body = opts.form;
  } else if (opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return res.json();
}

async function withSpin(btn, fn, label = "Aguarde…") {
  const labelEl = btn.querySelector(".label") || btn;
  const spin = btn.querySelector(".spinner-border");
  const original = labelEl.textContent;
  btn.disabled = true;
  labelEl.textContent = label;
  spin?.classList.remove("d-none");
  try { return await fn(); }
  finally {
    btn.disabled = false;
    labelEl.textContent = original;
    spin?.classList.add("d-none");
  }
}

// ---- Validacao & persistencia de cada passo --------------------------------

const STEP_HANDLERS = {
  async branding() {
    data.app_name = $("appName").value.trim();
    data.primary_color = $("primaryColor").value;
    if (!data.app_name) throw new Error("Informe o nome do sistema");
    await http("/api/setup/branding", { body: { app_name: data.app_name, primary_color: data.primary_color } });
    // logo upload (opcional)
    const file = $("logoFile").files[0];
    if (file) {
      const fd = new FormData(); fd.append("logo", file);
      await http("/api/setup/branding/logo", { form: fd });
      // atualiza preview
      $("brandLogo").src = "/api/branding/logo?" + Date.now();
      $("brandLogo").style.display = "";
    }
    setDone("branding");
  },

  async db() {
    data.db_url = $("dbUrl").value.trim();
    data.pool_size = parseInt($("poolSize").value || "20", 10);
    data.max_overflow = parseInt($("maxOverflow").value || "30", 10);
    if (!data.db_url) throw new Error("Informe a URL do banco");
    await http("/api/setup/db", { body: {
      db_url: data.db_url, pool_size: data.pool_size, max_overflow: data.max_overflow,
    }});
    setDone("db");
  },

  async smtp() {
    data.smtp = {
      host: $("smtpHost").value.trim(),
      port: parseInt($("smtpPort").value, 10),
      user: $("smtpUser").value.trim(),
      password: $("smtpPwd").value,
      sender: $("smtpFrom").value.trim() || $("smtpUser").value.trim(),
      use_tls: $("smtpTls").value === "true",
    };
    if (!data.smtp.host || !data.smtp.user) throw new Error("Preencha host e usuario do SMTP");
    await http("/api/setup/smtp", { body: data.smtp });
    setDone("smtp");
  },

  async admin() {
    const pwd  = $("adminPwd").value;
    const pwd2 = $("adminPwd2").value;
    if (pwd !== pwd2) throw new Error("Senhas nao conferem");
    if (pwd.length < 8) throw new Error("Senha precisa ter ao menos 8 caracteres");
    data.admin = {
      username: $("adminUser").value.trim(),
      email: $("adminEmail").value.trim(),
      full_name: $("adminFullName").value.trim() || null,
      password: pwd,
    };
    await http("/api/setup/admin", { body: data.admin });
    setDone("admin");
  },
};

// ---- Botoes globais (prev/next) -------------------------------------------

document.querySelectorAll("[data-next]").forEach(btn => btn.addEventListener("click", async () => {
  const name = STEPS[current];
  try {
    if (STEP_HANDLERS[name]) await withSpin(btn, () => STEP_HANDLERS[name](), "Salvando…");
    current = Math.min(current + 1, STEPS.length - 1);
    show(STEPS[current]);
  } catch (e) {
    alertMsg(e.message);
  }
}));

document.querySelectorAll("[data-prev]").forEach(btn => btn.addEventListener("click", () => {
  current = Math.max(current - 1, 0);
  show(STEPS[current]);
}));

// ---- Testes de conexao ----------------------------------------------------

$("btnTestDb").addEventListener("click", async () => {
  const btn = $("btnTestDb");
  await withSpin(btn, async () => {
    try {
      const r = await http("/api/setup/test/db", { body: {
        db_url: $("dbUrl").value, pool_size: 1, max_overflow: 0,
      }});
      if (r.ok) alertMsg("Conexao OK: " + r.detail, "success");
      else alertMsg("Falha: " + r.detail);
    } catch (e) { alertMsg(e.message); }
  }, "Testando…");
});

$("btnTestSmtp").addEventListener("click", async () => {
  const btn = $("btnTestSmtp");
  await withSpin(btn, async () => {
    try {
      const r = await http("/api/setup/test/smtp", { body: {
        host: $("smtpHost").value, port: parseInt($("smtpPort").value, 10),
        user: $("smtpUser").value, password: $("smtpPwd").value,
        sender: $("smtpFrom").value || $("smtpUser").value,
        use_tls: $("smtpTls").value === "true",
      }});
      if (r.ok) alertMsg("E-mail enviado: " + r.detail, "success");
      else alertMsg("Falha: " + r.detail);
    } catch (e) { alertMsg(e.message); }
  }, "Enviando…");
});

// ---- Review + Finalizar ---------------------------------------------------

function renderReview() {
  $("reviewBox").innerHTML = `
    <dl>
      <dt>Nome do sistema</dt><dd>${data.app_name || "(nao informado)"}</dd>
      <dt>Banco Protheus</dt><dd><code>${(data.db_url || "").replace(/:[^:@]+@/, ":***@")}</code></dd>
      <dt>SMTP</dt><dd>${(data.smtp?.user || "(nao configurado)")} @ ${data.smtp?.host || ""}</dd>
      <dt>Admin</dt><dd>${data.admin?.username || "(nao criado)"} · ${data.admin?.email || ""}</dd>
    </dl>
  `;
}

$("btnFinish").addEventListener("click", async () => {
  const btn = $("btnFinish");
  await withSpin(btn, async () => {
    try {
      await http("/api/setup/finish");
      alertMsg("Setup finalizado! Redirecionando para o login…", "success");
      setTimeout(() => { location.href = "login.html"; }, 1500);
    } catch (e) { alertMsg(e.message); }
  }, "Finalizando…");
});

// ---- Boot -----------------------------------------------------------------

(async () => {
  try {
    const state = await fetch("/api/setup/state").then(r => r.json());
    if (state.setup_complete) {
      alertMsg("Setup ja finalizado. Redirecionando…", "warning");
      setTimeout(() => { location.href = "login.html"; }, 1500);
      return;
    }
    state.completed_steps?.forEach(setDone);
  } catch (e) {
    alertMsg("Falha ao consultar estado do setup: " + e.message);
  }
  show("branding");
})();

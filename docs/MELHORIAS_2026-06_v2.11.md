# Melhorias v2.11.0 — 2026-06-12

Rodada de melhorias solicitada pela Fertimaxi. Cobre 5 pontos + limpeza de
código morto. Aplicado **no repo local e no servidor de produção** (192.168.0.219).

---

## 1. Controle de acesso por usuário (telas/menus) — **já existente, sem código novo**

O controle já existe e é aplicado em duas camadas:

| Camada | Onde | O que controla |
|---|---|---|
| **Papel (role)** | `frontend/js/layout.js` (menu) + `require_admin` nas rotas | Quais **telas/menus** aparecem. Operador (não-admin) vê só **Consultas Protheus** e **Agendamentos**; Dashboard, Auditor Fiscal, Perfis, Usuários, Auditoria e Administração são exclusivos de admin (e as rotas no backend rejeitam acesso não-admin). |
| **Ação** | `auth.hasAction("schedule")` | "Agendamentos" só aparece para quem tem a ação `schedule`. |
| **Perfis** | `profiles_seed.py` / tela Perfis & Módulos | Quais **tabelas Protheus** (aliases) o usuário enxerga nas Consultas. |

**Receita para "departamento só consulta + agenda":** crie o usuário como
**operador** (não admin), conceda a ação `schedule` e atribua o(s) **perfil(is)**
das tabelas permitidas. Ele verá apenas Consultas e Agendamentos.

---

## 2. Ícone de mostrar/ocultar senha — **corrigido**

**Sintoma:** apareciam 2 ícones; o nativo do Edge não fazia o esperado e o
segundo era um emoji 🙈 (não profissional).

**Causa:** o CSS forçava o olho **nativo** do Edge (`::-ms-reveal`) a aparecer
*e* o `pwd-toggle.js` injetava um 2º botão com emoji.

**Correção:**
- `frontend/css/style.css` — `::-ms-reveal`/`::-ms-clear` agora `display:none`
  (esconde o nativo); `.pwd-toggle` virou flex container para SVG.
- `frontend/js/pwd-toggle.js` — emojis 👁/🙈 trocados por **ícones SVG**
  profissionais (Bootstrap Icons eye / eye-slash), um único botão consistente
  em todos os navegadores, herdando a cor do tema.

---

## 3. Auditor Fiscal "puxando documentos anteriores" — **corrigido (substituir ao rodar)**

**Causa:** as anomalias (`fiscal_anomalies`) **acumulavam** a cada execução —
cada auditoria *adicionava* linhas sem apagar as antigas, então documentos de
auditorias passadas continuavam na tela.

**Correção (`backend/fiscal/auditor.py`):** novo `_purge_prior_anomalies()`
chamado no início de cada execução nos 3 motores (interno, financeiro,
comercial). Ele **substitui** os resultados anteriores do mesmo escopo:
- Com `chave_filter` (busca por NFe): apaga só aquela chave.
- Sem chave: apaga as **filiais auditadas** daquele **motor** (identificado pelo
  prefixo de `field_compared`: interno = sem prefixo, financeiro = `fin_`,
  comercial = `com_`).

Resultado: auditar "junho" mostra só junho; nada acumula de execuções anteriores.
O motor financeiro não apaga o interno e vice-versa (escopo por prefixo).

---

## 4. Botão "Limpar" — **verificado/esclarecido**

O **"🧹 Limpar Resultados" da tela do Auditor Fiscal** já era destrutivo de
verdade: chama `DELETE /api/fiscal/purge` e apaga **fisicamente** todas as
anomalias do banco (`backend/routers/fiscal_routes.py`). Não é só visual.

Atenção: o **"🧹 Limpar Formulário" da tela Consultas** é outro botão e, por
design, só reseta o formulário do Builder (não mexe em histórico). Com a
correção do ponto 3, o acúmulo que motivava o "limpar" deixa de acontecer.

---

## 5. Horário de Brasília (BRT) — **unificado**

**Causa:** mistura de fusos. As anomalias fiscais já gravavam em BRT, mas
**jobs, trilha de auditoria e janelas do dashboard** usavam `datetime.utcnow`
(UTC) e o **SO do servidor estava em UTC** — deixando horários ~3h fora,
especialmente nos KPIs/relatórios do Auditor perto da virada do dia.

**Correções:**
- `backend/models.py` — **todos** os `default/onupdate=datetime.utcnow` de
  timestamps de exibição/auditoria passam a gravar em **BRT**
  (`now_brt`). *Expiração de sessão/token (`ActiveSession.expires_at`,
  `PasswordResetToken.expires_at`) permanece em UTC — é setada explicitamente
  no `auth.py` e não passa pelos defaults.*
- `backend/jobs.py` — `started_at`/`finished_at` em BRT.
- `backend/scheduler.py` — `last_run_at` em BRT.
- `backend/routers/dashboard_routes.py` — janelas "hoje/últimos 7 dias/recentes"
  agora calculadas em BRT (casam com os dados gravados em BRT).
- **Servidor:** `timedatectl set-timezone America/Sao_Paulo` (logs do journald e
  qualquer `datetime.now()` naive passam a ser BRT).

> Nota: registros criados **antes** desta mudança estão em UTC; novos em BRT.
> Como o deploy é recente, o volume legado é pequeno.

---

## Limpeza de código (conservadora)

- **`requirements.txt`** — removida a dependência **`lxml`** (parsing de XML foi
  descontinuado no Sprint 12; nenhum `import lxml` restava no código).
- Verificados e **mantidos** por estarem em uso: `lgpd.py`, `fiscal/comparators.py`,
  `fiscal/webhook.py`, `reportlab`, `aiofiles`. (`odfpy` mantido por segurança —
  pode ser usado pelo pandas via engine ODS.)
- Os 3 motores de auditoria (interno, Financeiro SE2, Comercial SC5) foram
  **mantidos** (decisão: limpeza conservadora).

---

## Deploy

Arquivos alterados publicados em `/opt/protheus-reports` e serviços reiniciados:
`backend/models.py`, `backend/jobs.py`, `backend/scheduler.py`,
`backend/fiscal/auditor.py`, `backend/routers/dashboard_routes.py`,
`backend/version.py`, `requirements.txt`, `frontend/js/pwd-toggle.js`,
`frontend/css/style.css`. Versão **2.11.0** (build 2026-06-12).

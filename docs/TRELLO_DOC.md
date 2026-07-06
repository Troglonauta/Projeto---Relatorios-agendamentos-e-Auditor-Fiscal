# Documentação Técnica — Protheus Reports / Auditor Fiscal Fertimaxi

> **Projeto:** Protheus Reports (Auditor Fiscal)
> **Versão:** v2.6.0 · build 2026-05-28 · codename `internal-audit`
> **Linguagens / Stack:** Python 3.12 · FastAPI · SQLAlchemy · Celery · APScheduler · pyodbc · Bootstrap 5 · DataTables · Select2
> **Sistemas Impactados:** TOTVS Protheus (SQL Server) · SMTP corporativo · (Opcional) Webhook Teams/Slack
> **Repositório:** `/opt/protheus-reports` (produção Linux) ou `c:\protheus-reports` (dev Windows)

---

## Card 1 — Visão Geral, Objetivo e Usabilidade

### O que o projeto resolve

Plataforma web corporativa que executa **duas missões críticas** para a Controladoria Fertimaxi:

1. **Construtor de Consultas Protheus (Visual Query Builder)** — extração de relatórios das tabelas do ERP TOTVS Protheus (SF1, SC5, SE1, SE2, SA2, SB1, etc.) sem precisar abrir o Configurador, com filtros visuais, JOINs entre tabelas, dicionário SX3 humanizado, paginação, agendamento por e-mail, perfis de acesso e exportação em XLSX/CSV/PDF.

2. **Auditor Fiscal — Auditoria Interna SDS/SDT vs SF1/SD1** *(core comercial)* — cruzamento determinístico via SQL entre o XML internalizado pelo ERP (SDS = cabeçalho, SDT = itens) e a nota classificada (SF1 = cab. fiscal, SD1 = itens fiscais). Detecta divergências de **valor total, base/valor ICMS, alíquota, frete, seguro, desconto, despesas, CFOP, CST, quantidade, descrição** e principalmente **"Nota Ausente"** (XML que entrou no ERP mas não foi classificado).

### Escopo entregue (Sprints 1–16)

- [x] Construtor visual de queries com JOIN multi-tabela
- [x] Dicionário SX3 (headers humanizados)
- [x] Auditor Fiscal — motor 100% interno (sem SEFAZ/Alterdata/TSS)
- [x] Auditoria completa (relatório por chave: TODOS os campos com `status: ok / divergent / skipped`)
- [x] Fila assíncrona Celery + APScheduler para auditorias automáticas (cron)
- [x] Dashboard com KPIs e gráficos
- [x] Gestão de usuários, perfis, sessões ativas, LGPD masking
- [x] Setup Wizard inicial (5 passos)
- [x] White-label (logo, cor primária, app_name)
- [x] Excel formatado oficial (header verde, AutoFit, sort SF1→SD1→SDS→SDT)
- [x] E-mail autônomo com **planilha em anexo** (corpo curto gerencial)
- [x] Deploy Linux bare-metal (systemd + Nginx + Redis)

### Como o usuário final interage

A solução é uma **aplicação web** (browser) com layout 100% responsivo Bootstrap 5. Acesso por URL interna (ex: `http://auditor.fertimaxi.lan`).

**Perfis e telas:**

- **Administrador:** Setup Wizard, Admin (config DB/SMTP/Branding/Webhook), Usuários, Perfis, Sessões, Catálogo de Erros, Auditor Fiscal (full), Construtor, Dashboard.
- **Auditor Fiscal:** Auditor Fiscal (rodar auditoria, ver anomalias, gerar Relatório Completo por chave, exportar XLSX, ack/snooze).
- **Operador:** Construtor (visão simplificada), Consultas Salvas, Agendamentos.
- **Visualizador:** apenas Dashboard + relatórios já agendados.

### Passo a passo para iniciar e usar

**1. Primeiro acesso (Setup Wizard — apenas 1x na instalação):**

- [ ] Acessar `http://<servidor>/static/pages/setup.html`
- [ ] **Passo 1 — Branding:** nome do sistema + cor primária + logo PNG
- [ ] **Passo 2 — Banco Protheus:** URL SQLAlchemy `mssql+pyodbc://...` + Pool size (default 20+30)
- [ ] **Passo 3 — SMTP:** servidor/porta/usuário/senha + remetente + STARTTLS
- [ ] **Passo 4 — Admin:** usuário, e-mail, senha (mín. 8 caracteres)
- [ ] **Passo 5 — Finalizar:** marca `setup_complete=true` no banco

**2. Rotina diária do Auditor Fiscal:**

- [ ] Login em `http://<servidor>/static/pages/login.html`
- [ ] Acessar **Auditor Fiscal** no menu lateral
- [ ] Clicar em **"+ Nova auditoria"** → escolher período (Desde/Até) + filiais + tipos de documento (NF-e 55, CT-e 57, etc.)
- [ ] Aguardar o job processar (modal de progresso com ETA + cancelamento)
- [ ] Filtrar anomalias por **data / severidade / filial / NCM-only** — paginar [10, 20, 50, 100, Todos]
- [ ] Clicar em uma anomalia → modal lado a lado (Protheus × XML) → **Marcar como Ciente** ou **Snooze 1/7/30 dias**
- [ ] Exportar **XLSX** (header verde Fertimaxi) via dropdown 📥

**3. Relatório Completo (apresentação executiva):**

- [ ] Botão verde **📋 Relatório Completo** no topo do Auditor
- [ ] Informar **Filial** + **Chave NFe (44 dígitos)**
- [ ] Sistema mostra TODOS os ~21 campos auditados desse documento com tags **✓ Match** (verde), **✗ Divergente** (vermelho/amarelo) ou **— Sem dado** (cinza)

**4. Construtor de Consultas:**

- [ ] Acessar **Consultas Protheus** no menu
- [ ] Escolher **módulo + tabela base + filial** (ex: Faturamento → SF1 → 01)
- [ ] Opcional: adicionar JOINs (ex: SF1 com SA2 via F1_FORNECE = A2_COD)
- [ ] Marcar **colunas** (busca em tempo real via input + Select2)
- [ ] Adicionar **filtros** (`>=`, `=`, `LIKE`, `BETWEEN datas`, etc.)
- [ ] **Consultar** (preview paginado) ou **Baixar XLSX/CSV/PDF**
- [ ] Opcional: **Salvar Consulta** ou **Agendar** envio por e-mail (cron)

---

## Card 2 — Arquitetura, Funções e Rotinas

### Arquitetura em alto nível

```
                ┌──────────────┐
        HTTP    │  Nginx :80   │  proxy + static (/static/)
   ──────────► │  (reverse)   │
                └──────┬───────┘
                       │ 127.0.0.1:8000
                ┌──────▼────────┐    ┌────────────────┐
                │ Gunicorn +    │    │ Redis :6379    │
                │ UvicornWorker │───►│ (broker)       │
                │ (FastAPI)     │    └──────┬─────────┘
                │ + APScheduler │           │
                └──────┬────────┘           ▼
                       │            ┌────────────────┐
                       │            │ Celery Worker  │
                       │            │ - report_task  │
                       │            │ - fiscal_task  │
                       │            └────┬───────────┘
                       ▼                 │
              ┌──────────────┐           ▼
              │ SQLite       │    ┌───────────────┐
              │ data/app.db  │    │ SQL Server    │
              │ (AppSetting  │◄───┤ Protheus      │
              │  Fernet)     │    │ (pyodbc 1433) │
              └──────────────┘    └───────────────┘
```

### Estrutura de pastas (oficial)

```
protheus-reports/
├── backend/                       # API Python (FastAPI)
│   ├── main.py                   # Entry FastAPI (lifespan, routers, middleware)
│   ├── config.py                 # Settings via pydantic-settings (.env)
│   ├── database.py               # SQLAlchemy engine local (SQLite)
│   ├── models.py                 # 14 modelos ORM (User, AppSetting, Job, FiscalAnomaly...)
│   ├── schemas.py                # Pydantic DTOs request/response
│   ├── auth.py                   # JWT + bcrypt (hash de senha)
│   ├── deps.py                   # FastAPI dependencies (require_admin, get_db, ...)
│   ├── audit.py                  # AuditLog.log() — registra cada ação sensível
│   ├── lgpd.py                   # Mascaramento dinâmico por role
│   ├── protheus_api.py           # EngineRegistry (pool 20+30) para SQL Server
│   ├── protheus_aliases.py       # Whitelist de tabelas SF1, SC5, SE1, SE2, SA2, SB1
│   ├── dict_sx3.py               # Cache SX3 (X3_TITULO) — humaniza headers
│   ├── query_engine.py           # SQL builder com JOINs visuais
│   ├── reports.py                # XLSX/CSV/PDF (streaming + in-memory)
│   ├── xlsx_utils.py             # Helper compartilhado: build_formatted_xlsx_bytes()
│   ├── jobs.py                   # CRUD do Job (create_job, mark_running/done/failed)
│   ├── scheduler.py              # APScheduler (tick a cada N minutos)
│   ├── email_service.py          # SMTP + templates HTML + anexos in-memory
│   ├── timeutils.py              # now_brt() — timezone America/Sao_Paulo
│   ├── version.py                # VERSION = "2.6.0" + BUILD_DATE
│   ├── errors.py                 # AppError + catálogo de códigos ERR-*
│   ├── profiles_seed.py          # Seed dos 8 perfis canônicos
│   ├── security/                 # Fernet + settings_store + master_key
│   │   ├── crypto.py             # ensure_master_key() + encrypt/decrypt
│   │   └── settings_store.py     # AppSetting CRUD com cache + invalidate
│   ├── routers/                  # 13 routers (FastAPI APIRouter)
│   │   ├── auth_routes.py        # /api/auth/login, /forgot-password
│   │   ├── users_routes.py       # /api/users (CRUD)
│   │   ├── profiles_routes.py    # /api/profiles (perfis canônicos)
│   │   ├── protheus_routes.py    # /api/protheus/query, /aliases
│   │   ├── saved_queries_routes # /api/saved-queries
│   │   ├── schedules_routes.py   # /api/schedules (agendamento)
│   │   ├── jobs_routes.py        # /api/reports/jobs (fila)
│   │   ├── fiscal_routes.py      # /api/fiscal/* (auditor + document-audit)
│   │   ├── dashboard_routes.py   # /api/dashboard/today, /fiscal-recent
│   │   ├── admin_routes.py       # /api/admin/* (reload, restart, health/detail)
│   │   ├── settings_routes.py    # /api/settings/public (white-label)
│   │   ├── setup_routes.py       # /api/setup/* (wizard)
│   │   └── audit_routes.py       # /api/audit-logs (auditoria interna)
│   ├── fiscal/                   # Motor do Auditor Fiscal
│   │   ├── internal_audit.py     # _load_audit_period_internal (SQL JOIN triplo)
│   │   ├── rule_engine.py        # FiscalRuleEngine (12 regras header + 9 itens)
│   │   ├── comparators.py        # Divergence + tolerâncias (tol_valor/icms/qtd)
│   │   ├── auditor.py            # run_audit() — orquestrador
│   │   ├── webhook.py            # send_critical_alert() — Teams/Slack
│   │   └── templates/anomaly_report.html  # E-mail HTML detalhado (preview)
│   ├── queue/                    # Celery
│   │   ├── celery_app.py         # Resolução dinâmica de broker (Redis ou SQLite)
│   │   └── tasks/
│   │       ├── report_task.py    # Task generate_report
│   │       └── fiscal_task.py    # Task run_fiscal_audit
│   ├── cli/                      # Scripts CLI
│   │   └── rotate_master_key.py  # Re-criptografia em massa
│   └── email_templates/          # Templates HTML transacionais
│
├── frontend/                     # SPA estática (servida por Nginx em /static/)
│   ├── pages/                    # login.html, dashboard.html, fiscal.html, ...
│   ├── js/                       # api.js, auth.js, layout.js, fiscal.js, protheus.js
│   ├── css/style.css             # CSS + dark mode + identidade Fertimaxi
│   └── img/                      # logo + favicon + background
│
├── data/                         # PERSISTENTE — backup crítico
│   ├── app.db                    # SQLite (todos os dados de negócio)
│   ├── branding/logo.png         # Logo white-label
│   └── secrets/                  # Arquivos sensíveis (.pfx, etc.)
│
├── reports_output/               # XLSX/CSV gerados pelo worker
├── docs/                         # Documentação técnica (este arquivo, sprints, etc.)
├── scripts/                      # supervisor.py, seed_admin.py, start.py
├── requirements.txt              # Dependências Python
├── .env                          # Segredos (não versionado) — MASTER_KEY, JWT_SECRET
└── run.py                        # Entry point dev (uvicorn)
```

### Mapeamento de módulos críticos (o que cada um faz)

**Backend — Core**

- [ ] `backend/main.py` → bootstrap do FastAPI, lifespan que executa: `ensure_master_key()`, `Base.metadata.create_all`, migração `.env→AppSetting`, seed de perfis, pré-load SX3, `scheduler.start()`.
- [ ] `backend/config.py` → `Settings(BaseSettings)` com 20+ variáveis de ambiente. `@lru_cache get_settings()`.
- [ ] `backend/database.py` → `engine = create_engine(DATABASE_URL)` + `SessionLocal`.
- [ ] `backend/models.py` → **14 modelos ORM** mapeados em SQLite.
- [ ] `backend/auth.py` → `hash_password()` (bcrypt), `verify_password()`, `create_access_token()` (JWT HS256, exp 480 min), `decode_token()`.
- [ ] `backend/deps.py` → dependencies do FastAPI: `get_db`, `get_current_user`, `require_admin`, `require_setup_complete`, `get_client_ip`.
- [ ] `backend/audit.py` → função `log(action, user, ip, detail, success)` → grava em tabela `AuditLog` (forensics).

**Backend — Protheus**

- [ ] `backend/protheus_api.py` → `EngineRegistry` singleton thread-safe, pool 20+30 com `pool_recycle=1800`. Método `reset()` recria após reload de config.
- [ ] `backend/protheus_aliases.py` → whitelist de tabelas permitidas + função `is_known_alias()`, `branch_field()`, mapeamento alias → módulo.
- [ ] `backend/query_engine.py` → builder SQL com escape paramétrico, suporte a JOINs múltiplos (até 4), filtros AND, WHERE + WITH (NOLOCK).
- [ ] `backend/dict_sx3.py` → cache do dicionário SX3 do Protheus (`X3_CAMPO` → `X3_TITULO`). `humanize_header(col)` traduz `C5_EMISSAO` → "Data de Emissão (C5_EMISSAO)".

**Backend — Motor Fiscal (Sprint 12-13)**

- [ ] `backend/fiscal/internal_audit.py:_load_audit_period_internal()` → 1 query SQL principal `SDS LEFT JOIN SF1 ON (F1_CHVNFE = DS_CHAVENF OR F1_DOC+SERIE+FORNECE+LOJA = DS_DOC+SERIE+FORNEC+LOJA)` + batch SDT/SD1/SA2. **JOIN triplo fallback** para releases antigas com `F1_CHVNFE` vazio.
- [ ] `backend/fiscal/internal_audit.py:_detect_sdt_columns()` → consulta `INFORMATION_SCHEMA.COLUMNS` em runtime para mapear `DT_XBASICM` ↔ `DT_BASEICM`, `DT_XMLICM` ↔ `DT_VALICM`, etc.
- [ ] `backend/fiscal/rule_engine.py:FiscalRuleEngine.run()` → executa 12 regras de cabeçalho + 9 regras por item. Retorna lista de `{field, label, protheus_value, xml_value, status, severity, note, category, item_n}`. Status: `ok` / `divergent` / `skipped`.
- [ ] `backend/fiscal/auditor.py:run_audit()` → orquestrador: itera filiais, chama loader, roda engine, persiste só `divergent` em `FiscalAnomaly`, envia e-mail com **anexo XLSX in-memory**.
- [ ] `backend/fiscal/auditor.py:_render_email_executive()` → corpo HTML curto (~2.8 KB) com KPIs + CTA do anexo.
- [ ] `backend/fiscal/auditor.py:_build_anomalies_xlsx_bytes()` → gera XLSX em memória via helper compartilhado.
- [ ] `backend/fiscal/comparators.py` → `Divergence` dataclass + tolerâncias configuráveis em `AppSetting` (`FISCAL_TOLERANCE_VALOR_RS=0.05`, `FISCAL_TOLERANCE_ICMS_RS=0.02`, `FISCAL_TOLERANCE_QUANT=0.01`).
- [ ] `backend/fiscal/webhook.py` → POST JSON para URL Slack/Teams ao final de auditoria com ≥1 crítica.

**Backend — Fila & Agendamento**

- [ ] `backend/queue/celery_app.py` → resolução dinâmica do broker (Redis se disponível, fallback SQLite-Kombu para dev).
- [ ] `backend/queue/tasks/fiscal_task.py:run_fiscal_audit()` → task Celery que decodifica payload do `Job`, chama `auditor.run_audit()`, persiste stats JSON em `reports_output/`.
- [ ] `backend/queue/tasks/report_task.py:generate_report()` → executa SQL no Protheus em chunks de 10.000 linhas, grava XLSX/CSV em streaming.
- [ ] `backend/scheduler.py` → APScheduler com 2 jobs: `tick` (agendamentos de relatórios cron) e `fiscal_tick` (auditoria autônoma diária).

**Backend — Helpers de Output**

- [ ] `backend/xlsx_utils.py:build_formatted_xlsx_bytes()` → helper unificado: header verde `#2E8B3D` bold center, AutoFit, freeze A2, AutoFilter, ordenação SF1→SD1→SDS→SDT.
- [ ] `backend/reports.py:write_xlsx_stream()` → versão streaming para datasets >50k linhas.
- [ ] `backend/email_service.py:send_email()` → SMTP via `smtplib` + `EmailMessage`. Aceita anexos como `Path` ou `dict(filename, content: bytes, mimetype)`.

**Backend — Segurança**

- [ ] `backend/security/crypto.py:ensure_master_key()` → garante `MASTER_KEY` Fernet (gera se ausente, grava em `.env` write-once).
- [ ] `backend/security/settings_store.py:get_setting()` / `set_setting()` → CRUD do AppSetting com cache LRU + invalidação. Segredos cifrados via Fernet.

**Frontend (SPA estática)**

- [ ] `frontend/js/api.js` → wrapper `fetch()` com auth header + tratamento de 401 + spinner helpers.
- [ ] `frontend/js/auth.js` → idle watcher (auto-logout 20min), `isAdmin()`, `touchActivity()`.
- [ ] `frontend/js/layout.js` → renderiza sidebar dinâmica por role + topbar + clock BRT.
- [ ] `frontend/js/fiscal.js` → tela do Auditor Fiscal: DataTables paginado, modal Relatório Completo, ack/snooze, export.
- [ ] `frontend/js/protheus.js` → Construtor: visual query builder, JOINs, Select2 nos filtros, salvar/agendar consulta.

### Bibliotecas, frameworks e dependências

**Backend Python:**

| Lib                 | Versão  | Para quê |
|---|---|---|
| `fastapi`           | 0.115.0  | API HTTP + auto docs OpenAPI |
| `uvicorn[standard]` | 0.30.6   | Server ASGI |
| `gunicorn`          | (prod)   | Process manager + UvicornWorker |
| `sqlalchemy`        | 2.0.35   | ORM e engine pool |
| `pydantic`          | 2.9.2    | Validação de schemas |
| `pydantic-settings` | 2.5.2    | Settings via `.env` |
| `python-jose`       | 3.3.0    | JWT (HS256) |
| `passlib[bcrypt]` + `bcrypt` 4.0.1 | — | Hash de senha |
| `pyodbc`            | 5.1.0    | Driver SQL Server (Protheus) |
| `pandas`            | 2.2.3    | DataFrames + `read_sql(chunksize=)` |
| `openpyxl`          | 3.1.5    | Geração XLSX formatado |
| `odfpy`             | 1.4.1    | Geração ODS |
| `reportlab`         | 4.2.5    | Geração PDF |
| `apscheduler`       | 3.10.4   | Agendamento in-process (cron) |
| `celery`            | 5.4.0    | Fila assíncrona |
| `redis`             | 5.0.8    | Cliente Redis (broker) |
| `cryptography`      | >=43.0.0 | Fernet (criptografia AppSetting) |
| `aiofiles`          | 24.1.0   | I/O async (upload de logo) |
| `email-validator`   | 2.2.0    | Validação de e-mail |
| `python-multipart`  | 0.0.12   | Uploads multipart no Wizard |

**Frontend (CDN):**

- Bootstrap 5.3.3 (CSS + JS bundle)
- jQuery 3.7.1 (necessário por DataTables e Select2)
- DataTables 2.1.8 (paginação + filtros) + tema Bootstrap-5
- Select2 4.1.0-rc.0 + tema bootstrap-5 (busca em selects)
- Chart.js (gráficos do dashboard)
- (Removido Sprint 14) Choices.js — substituído por Select2

**Infra (produção Linux):**

- Ubuntu Server 24.04 LTS
- Microsoft ODBC Driver 18 for SQL Server + `unixodbc-dev`
- Redis 7 (broker do Celery)
- Nginx (proxy reverso + estáticos)
- systemd (resiliência + boot automático)

---

## Card 3 — Operações, Banco de Dados e Integrações

### Fluxo das operações principais

**Fluxo 1 — Construtor de Consultas (sob demanda):**

```
Usuário → Frontend (/api/protheus/query)
       → FastAPI valida perfil + tabela permitida
       → query_engine constrói SQL com NOLOCK + bind params
       → protheus_api.engine_registry.get() retorna pool conn
       → pyodbc executa no SQL Server Protheus
       → Resposta JSON paginada (preview) OU
       → POST /api/reports/jobs (>50k linhas) → Celery worker
       → worker grava XLSX/CSV em reports_output/
       → Job.status='done' → frontend permite download
```

**Fluxo 2 — Auditoria Fiscal (cron diário ou sob demanda):**

```
APScheduler.fiscal_tick (06:00) OU usuário clica "+ Nova auditoria"
   → POST /api/fiscal/audit/run
   → Cria Job no banco SQLite
   → Enfileira via Celery (Redis)
   → Worker pega o job e executa fiscal_task.run_fiscal_audit()
   → auditor.run_audit() para cada filial:
       a) _load_audit_period_internal() — 1 query SDS LEFT JOIN SF1 (com fallback)
       b) Para cada doc, _detect_sdt_columns + batch SDT/SD1/SA2
       c) FiscalRuleEngine(doc).run() → 21+ campos por documento
       d) Filtra status='divergent' → persiste em FiscalAnomaly
   → Gera XLSX in-memory com anomalias (header verde)
   → SMTP envia e-mail HTML curto + anexo XLSX
   → POST opcional para webhook Slack/Teams (se ≥1 crítica)
   → Job.status='done' + stats JSON em reports_output/
```

**Fluxo 3 — Relatório Completo (apresentação executiva):**

```
Usuário clica "📋 Relatório Completo" → modal pede branch + chave NFe
   → GET /api/fiscal/document-audit?branch=01&chave=...
   → Backend chama _load_audit_period_internal(chave_filter=chave)
   → FiscalRuleEngine(doc).run() → relatório completo (~21 campos)
   → Retorna JSON com {summary, counts:{ok, divergent, skipped}, report}
   → Frontend renderiza tabela com tags verde/vermelho/cinza
   → NÃO persiste em banco (read-only audit)
```

### Tabelas Protheus consultadas

Todas via leitura `SELECT ... WITH (NOLOCK)` (zero impacto no ERP).

| Tabela | Coluna chave | Uso principal |
|---|---|---|
| **SF1**xx0 | F1_FILIAL, F1_DOC, F1_SERIE, F1_FORNECE, F1_LOJA, F1_CHVNFE | Cabeçalho fiscal (nota classificada) |
| **SD1**xx0 | D1_DOC, D1_SERIE, D1_FORNECE, D1_LOJA, D1_ITEM | Itens da nota classificada |
| **SDS**xx0 | DS_FILIAL, DS_DOC, DS_SERIE, DS_FORNEC, DS_LOJA, DS_CHAVENF, DS_EMISSA | Cabeçalho XML internalizado |
| **SDT**xx0 | DT_FILIAL, DT_DOC, DT_SERIE, DT_FORNEC, DT_LOJA, DT_ITEM | Itens do XML internalizado |
| **SA2**xx0 | A2_COD, A2_LOJA, A2_CGC | Cadastro de fornecedor (para CNPJ) |
| **SB1**xx0 | B1_COD, B1_DESC | Cadastro de produto (Construtor) |
| **SC5**xx0 | C5_NUM, C5_CLIENTE | Pedidos de venda (Construtor) |
| **SE1**xx0 | E1_PREFIXO, E1_NUM | Contas a receber (Construtor) |
| **SE2**xx0 | E2_PREFIXO, E2_NUM | Contas a pagar (Construtor) |
| **SX3**xx0 | X3_CAMPO, X3_TITULO | Dicionário (humanização de headers) |

**Padrão de nomenclatura:** Alias + Filial + Sufixo. Ex: `SE1+01+0 = SE1010`. Sufixo configurável via `PROTHEUS_TABLE_SUFFIX` (default `0`).

### Banco local (SQLite) — 14 modelos ORM

Arquivo: `data/app.db` (caminho configurado em `DATABASE_URL`).

| Modelo                | Para quê |
|---|---|
| `User`                | Usuários da plataforma (role: admin/auditor/operator/viewer) |
| `Profile`             | 8 perfis canônicos (Faturamento, Compras, Financeiro, Estoque, Logística, Fiscal, Comercial, RH) |
| `TableProfile`        | Whitelist de tabelas por perfil |
| `UserProfile`         | M:N user × profile |
| `UserTablePermission` | Override fine-grained (admin libera tabela X para user Y) |
| `UserActionPermission`| Permissões de ação (view/export/schedule) |
| `AuditLog`            | Trilha de auditoria (login, query, export, anomaly ack, etc.) |
| `ScheduledReport`     | Agendamentos de relatórios (cron) |
| `AppSetting`          | Configurações cifradas (SMTP_PASSWORD, JWT, NFSTOCK_TOKEN legado, etc.) |
| `Job`                 | Estado de jobs assíncronos (queued/running/done/failed/canceled) |
| **`FiscalAnomaly`**   | **Divergências do Auditor (doc_key, branch, field, protheus_value, xml_value, severity)** |
| `ActiveSession`       | Sessões JWT (jti, expires_at, revoked_at) — máx. 3 por user |
| `SavedQuery`          | Consultas salvas pelo Construtor |
| `PasswordResetToken`  | Tokens de "esqueci minha senha" (2h de validade) |

### Integrações externas

**1. SQL Server Protheus (1433/TCP — saída):**
- Driver: ODBC Driver 18 for SQL Server (Linux) / 17 (Windows)
- Pool SQLAlchemy: 20 conexões + 30 overflow, recycle 1800s
- Leitura apenas (zero `UPDATE`/`INSERT`/`DELETE` no Protheus)

**2. SMTP corporativo (587 ou 465/TCP — saída):**
- `smtplib.SMTP` com STARTTLS por default
- Templates HTML em `backend/email_templates/`
- Anexos in-memory (XLSX gerado via `io.BytesIO`)

**3. Webhook Slack/Teams (HTTPS — saída opcional):**
- `FISCAL_WEBHOOK_URL` em AppSetting (cifrada com Fernet)
- POST JSON ao final de auditoria com ≥1 crítica
- Best-effort (falha não bloqueia o job)

**4. Frontend → Backend:**
- REST JSON sobre HTTPS (em prod) ou HTTP local
- Auth: Bearer JWT no header `Authorization`
- Polling de jobs a cada 3s com backoff exponencial (cap 15s)

---

## Card 4 — Segurança, Criptografia e Tratamento de Erros

### Gestão de credenciais e segredos

**Camada 1 — `.env` (não versionado):**

- `MASTER_KEY` — chave Fernet (32 bytes base64), gerada uma única vez na instalação
- `JWT_SECRET` — chave HS256 (48 bytes hex)
- `PROTHEUS_DB_URL` — URL com usuário/senha do SQL Server
- `SMTP_PASSWORD` — senha do SMTP
- Permissão: **600** (`chown protheus:protheus`)

**Camada 2 — AppSetting (SQLite cifrado com Fernet):**

Segredos pós-Wizard são gravados na tabela `AppSetting` cifrados pela `MASTER_KEY`:

```python
settings_store.set_setting("SMTP_PASSWORD", "secret123", is_secret=True)
# Internamente:  Fernet(master_key).encrypt(b"secret123")
# Coluna no banco: AppSetting.encrypted_value (Text)
```

Leitura: cache LRU + invalidação automática em `set_setting()` e `reload-config`.

**Camada 3 — Senhas de usuário:**

- Hash via `bcrypt` (rounds 12, custo ~250ms)
- `must_change_password=True` força troca no 1º login
- Tokens de reset com `secrets.token_urlsafe(32)` + expiração 2h

**Camada 4 — JWT:**

- Algoritmo HS256 (chave simétrica `JWT_SECRET`)
- Expiração 480 min (8h) — `JWT_EXPIRE_MINUTES`
- `jti` único por sessão → revogação granular via `ActiveSession.revoked_at`
- Limite de 3 sessões ativas simultâneas por usuário

### Métodos de segurança aplicados

- [x] **Senhas:** bcrypt + salt automático
- [x] **Tokens:** JWT HS256 + revogação por jti
- [x] **Segredos no banco:** Fernet (AES-128-CBC + HMAC-SHA256)
- [x] **CSRF:** N/A (API com Bearer token, não cookies)
- [x] **CORS:** middleware ajustado para origem da intranet
- [x] **SQL Injection:** **100% via bind params do SQLAlchemy** (`text(sql).bindparams(...)`)
- [x] **XSS:** sanitização inline + `replace(/</g, "&lt;")` em valores renderizados
- [x] **LGPD masking:** `backend/lgpd.py` mascara CPF/CNPJ/e-mail dinamicamente para roles operator/viewer
- [x] **Permissões em camadas:**
  - Whitelist de tabelas Protheus por perfil
  - `require_admin` dependency em rotas sensíveis
  - `UserActionPermission` (view/export/schedule)
- [x] **Idle logout:** frontend dispara logout após 20min sem interação
- [x] **Hardening systemd:** `NoNewPrivileges=true`, `ProtectSystem=strict`, `ReadWritePaths=...` limitado
- [x] **Nginx limite de upload:** `client_max_body_size 64M`
- [x] **TLS:** quando habilitado, force HTTPS via 301 da porta 80

### Tratamento de erros — catálogo + recuperação

Cada erro previsível mapeado para um código `ERR-*` em `backend/errors.py`. Documentado em `docs/ERROR_CATALOG.md`.

| Código | Significado | Ação automática |
|---|---|---|
| `ERR-DB-001` | SQL Server inacessível (timeout/refused) | Engine pool tenta reconectar; UI mostra banner |
| `ERR-DB-002` | Tabela/coluna inexistente (pyodbc.ProgrammingError) | `_is_missing_table_or_col()` silencia + log debug |
| `ERR-DB-003` | Login SQL falhou | Fail-fast no boot; admin precisa rever `.env` |
| `ERR-AUTH-001` | Token JWT inválido/expirado | 401 → frontend redireciona para login |
| `ERR-AUTH-002` | Senha incorreta | 5 tentativas → conta bloqueada 15min |
| `ERR-AUTH-003` | Sessão revogada | 401 + mensagem "Sua sessão foi encerrada por outro dispositivo" |
| `ERR-SMTP-001` | SMTP inacessível | E-mail vai para fila de retry; job continua |
| `ERR-SMTP-002` | Credenciais SMTP inválidas | Notifica admin via AuditLog |
| `ERR-JOB-001` | Fila Redis indisponível | Job marcado `failed`; UI sugere `/admin/health/detail` |
| `ERR-JOB-002` | Worker matado durante job | Worker no boot varre jobs órfãos → `failed` |
| `ERR-JOB-003` | Cancelamento solicitado | Worker checa flag entre chunks; rm arquivo `.tmp` |
| `ERR-FISCAL-001` | Tabela SDS/SDT/SF1 inexistente | Log warning, retorna lista vazia (não trava) |
| `ERR-FISCAL-005` | Erro genérico no motor | Job falha mas próximas execuções continuam |
| `ERR-CFG-001` | MASTER_KEY ausente | Gera nova + grava no `.env` (1ª vez) |
| `ERR-CFG-002` | MASTER_KEY trocada → segredos ilegíveis | Erro fatal, sugere `rotate_master_key` CLI |

### Resiliência operacional

- [x] **EngineRegistry com `reset()`:** após `reload-config`, recria pool sem restart do processo
- [x] **APScheduler isola exceções por tick:** falha em 1 agendamento não derruba os outros
- [x] **Celery worker `--max-tasks-per-child=200`:** recicla processo (evita memory leak)
- [x] **systemd `Restart=on-failure`:** crash → reinicia em 5s
- [x] **Redis `Requires=redis-server.service`:** systemd garante ordem de boot
- [x] **Cancelamento atômico de jobs:** worker grava em `.tmp` + `os.rename()` no final; on-cancel `rm .tmp`
- [x] **Migração graceful de schema:** colunas novas detectadas em runtime (SDT XBASICM/BASEICM)

### Logs e observabilidade

- [x] **journald (systemd):** todos os stdout/stderr dos services
  - `journalctl -u protheus-reports-web -f`
  - `journalctl -u protheus-reports-worker -f`
- [x] **Nginx:** `/var/log/nginx/protheus-reports.access.log` + `.error.log`
- [x] **AuditLog (DB):** trilha forense de ações sensíveis (login, query, export, ack, restart, reload)
- [x] **/api/admin/health/detail:** endpoint que retorna status de Protheus + Redis + Scheduler + Jobs por status
- [x] **/api/health (público):** liveness check para load balancer/healthcheck

### Recuperação de desastre

- [ ] **Backup diário** de `data/app.db` + `data/branding/` + `data/secrets/` em `/var/backups/protheus-reports/` (retenção 30d)
- [ ] **Backup separado do `.env`** em cofre Fertimaxi (contém `MASTER_KEY` + `JWT_SECRET`)
- [ ] **CLI de rotação:** `python -m backend.cli.rotate_master_key OLD NEW` re-criptografa todos os segredos
- [ ] **Restore:** copiar `app.db` de volta + `.env` original → restart dos services → sistema volta exatamente onde parou

---

## Resumo executivo para o quadro

| Card | Tema | Status |
|---|---|---|
| 1 | Visão geral, objetivo, usabilidade | ✅ |
| 2 | Arquitetura, módulos, dependências | ✅ |
| 3 | Operações, banco, integrações | ✅ |
| 4 | Segurança, criptografia, erros | ✅ |

**Sistema em produção desde:** 2026-05-28 · **Versão atual:** v2.6.0 (`internal-audit`) · **Sprints entregues:** 16

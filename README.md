# Protheus Reports — Fertimaxi

**v2.0.0** · Code Freeze · build 2026-05-21

Plataforma comercial **white-label** para extração, agendamento e auditoria
fiscal de dados do **TOTVS Protheus**, com:

- **Builder Visual** estilo APSDU (sem digitar SQL nem nome de tabela física)
- **Fila Celery** para relatórios pesados (300k+ linhas) sem travar a UI
- **Auditor Fiscal** comparando NFe (TOTVS Transmite) × 8 tabelas Protheus, com validador forte de **NCM** (compliance)
- **Setup Wizard** white-label (cliente configura tudo pela UI, zero `.env`)
- **Perfis/Módulos** (Logística, Contábil, Controladoria, Financeiro, PCP, Estoque, Administrativo, Comercial) + RBAC granular
- **Hot-reload** de configuração + restart controlado via UI
- 32+ códigos de erro mapeados, idle logout, limite de sessões concorrentes

> Stack: **FastAPI · SQLAlchemy · Pandas · Celery · Redis · APScheduler · Bootstrap 5 · Chart.js**

---

## Dois modos de operação

### 🧪 Dev local (Windows/Linux)
**Zero config** — broker Celery cai para SQLite automaticamente quando Redis
não está disponível.

```powershell
.\.venv\Scripts\Activate.ps1   # Linux: source .venv/bin/activate #Quando ele está ativado, qualquer biblioteca que você instale (como o pyodbc para conectar no Protheus, o FastAPI ou o Celery) fica salva apenas dentro da pasta .venv do projeto, e não no seu Windows inteiro. Isso impede que um projeto quebre o outro.
python scripts\start.py
```

Sobe **Web + Worker no mesmo terminal** com prefixos coloridos e Ctrl+C
encerra os dois. Acesse `http://localhost:8000/`.

### 🏢 Produção
**LXC bare metal no Proxmox** com `systemd` + Nginx + Redis nativo.
Guia completo passo-a-passo: **[`docs/DEPLOY_LXC.md`](docs/DEPLOY_LXC.md)**.

Footprint medido em idle: ~215 MB de RAM.

---

## Documentação por tópico

| Tópico | Arquivo |
|---|---|
| 🚀 **Implantação produção (LXC Proxmox)** | [`docs/DEPLOY_LXC.md`](docs/DEPLOY_LXC.md) |
| 🐞 **Catálogo de erros** (códigos `ERR-XXX-NNN`) | [`docs/ERROR_CATALOG.md`](docs/ERROR_CATALOG.md) |
| 📜 **Histórico das fases** | [`docs/FASE3_PROGRESS.md`](docs/FASE3_PROGRESS.md), [`docs/PHASE4A_PROGRESS.md`](docs/PHASE4A_PROGRESS.md), [`docs/PHASE4B_BACKEND.md`](docs/PHASE4B_BACKEND.md) |
| 🎯 **Plano técnico da Fase 4** | [`docs/PHASE4_PLAN.md`](docs/PHASE4_PLAN.md) |
| 🧪 **QA rounds** | [`docs/QA_ROUND_1_REPORT.md`](docs/QA_ROUND_1_REPORT.md) |

---

## Estrutura do código

```
protheus-reports/
├── backend/
│   ├── main.py                    # FastAPI + lifespan condicional (setup wizard)
│   ├── config.py                  # Settings via .env + AppSetting shim
│   ├── database.py / models.py    # SQLite local: users, perfis, jobs, anomalias…
│   ├── auth.py / deps.py          # JWT com jti + limite de sessões + RBAC
│   ├── protheus_api.py            # EngineRegistry pool 20+30 (hot-reload)
│   ├── protheus_aliases.py        # Catálogo de 80+ aliases Protheus
│   ├── profiles_seed.py           # 8 perfis canônicos + matriz default
│   ├── reports.py                 # Streaming xlsx (openpyxl write_only) + csv
│   ├── jobs.py                    # CRUD do model Job
│   ├── scheduler.py               # APScheduler + fiscal_tick
│   ├── email_service.py           # SMTP (settings_store)
│   ├── errors.py                  # AppError + 33 códigos catalogados
│   ├── version.py                 # VERSION + BUILD_DATE
│   ├── security/                  # Fernet + AppSetting criptografado
│   ├── fiscal/                    # Auditor Fiscal (Sprint 4.B)
│   │   ├── auditor.py             # Batch loaders das 8 tabelas + comparators
│   │   ├── comparators.py         # Funções puras com tolerância configurável
│   │   ├── xml_sources/           # TransmiteSource (default), TSS, A1 stub, NFSTOCK
│   │   └── templates/             # E-mail HTML com seção NCM destacada
│   ├── queue/                     # Celery app + tasks
│   ├── cli/                       # rotate_master_key
│   └── routers/                   # auth, users, perfis, protheus, schedules,
│                                  # jobs, fiscal, dashboard, admin, audit, setup
├── frontend/
│   ├── pages/                     # login, setup, dashboard, protheus, schedules,
│   │                              # users, audit, fiscal, profiles, admin
│   ├── js/                        # ES6 modules
│   └── css/                       # tema Fertimaxi com paleta dinâmica
├── scripts/
│   ├── start.py                   # Dev — sobe web + worker em 1 terminal
│   ├── supervisor.py              # Dev — watchdog para botão "Reiniciar"
│   └── seed_admin.py              # Cria admin inicial via CLI
├── docs/                          # Manuais e progress reports
├── data/                          # Volume — SQLite + branding (não versionado)
└── reports_output/                # Volume — arquivos gerados pelo worker
```

---

## Como configurar pela primeira vez

1. **Suba o serviço** (`python scripts/start.py` no dev, ou systemd em prod).
2. Abra `http://<host>/` → será redirecionado para o **Setup Wizard**.
3. Complete os 6 passos: branding, banco Protheus, SMTP, APIs externas, admin inicial, finalizar.
4. Login com o admin criado. A partir daqui, **toda configuração é editável** pela
   aba **Administração > Configurações** (sem mexer no `.env`).

---

## Endpoints — referência rápida

| Categoria | Exemplos |
|---|---|
| Auth | `POST /api/auth/login`, `POST /api/auth/logout`, `POST /api/auth/change-password` |
| Setup | `GET/POST /api/setup/*` (só ativo enquanto `setup_complete=false`) |
| Protheus | `GET /api/protheus/aliases`, `GET /api/protheus/columns?alias=&branch=`, `POST /api/protheus/query` |
| Jobs (fila) | `POST /api/reports/jobs`, `GET /api/reports/jobs/{id}`, `GET /api/reports/jobs/{id}/download`, `DELETE /api/reports/jobs/{id}` |
| Auditor Fiscal | `POST /api/fiscal/audit/run`, `GET /api/fiscal/anomalies`, `POST /api/fiscal/config/test-source` |
| Dashboard | `GET /api/dashboard/today`, `GET /api/dashboard/fiscal-recent`, `GET /api/dashboard/feed` |
| Perfis | `GET/POST/PUT/DELETE /api/profiles`, `POST /api/profiles/{id}/tables`, `PUT /api/users/{id}/profiles` |
| Admin | `POST /api/admin/reload-config`, `POST /api/admin/restart`, `GET /api/admin/health/detail`, `GET /api/admin/error-catalog`, `POST /api/admin/config/{branding,db,smtp,apis,fiscal,operation}` |
| Schedules | `GET/POST/DELETE /api/schedules`, `POST /api/schedules/{id}/run-now` (via Celery) |

Documentação OpenAPI interativa em `/docs` (apenas para admins, naturalmente).

---

## Backup obrigatório

Sempre faça backup conjunto dos 3 artefatos:

```
/opt/protheus-reports/.env           ← contém MASTER_KEY
/opt/protheus-reports/data/app.db    ← settings cifrados + users + jobs
/opt/protheus-reports/data/branding/ ← logo customizado
```

**Perder a `MASTER_KEY` = perder todas as credenciais cifradas** (Protheus DB,
SMTP, Transmite, A1, NFSTOCK). Para rotacionar com segurança:

```bash
.venv/bin/python -m backend.cli.rotate_master_key --dry-run
.venv/bin/python -m backend.cli.rotate_master_key
systemctl restart protheus-reports-web protheus-reports-worker
```

---

## Licença

Privado / interno — Fertimaxi Comércio.

# Fase 3 — Estado da entrega (Sprints 1, 2, 3 e 4 COMPLETAS)

## Como rodar (dev local — zero config, sem Redis)

```powershell
.\.venv\Scripts\Activate.ps1
python scripts\start.py
```

Em **um único terminal** sobe Web (com supervisor) + Worker Celery juntos. Ctrl+C derruba os dois. Output prefixado por `[WEB ]` e `[WORKER]` coloridos.

Quando o `start.py` sobe sem Redis local, o broker do Celery cai automaticamente em `sqla+sqlite:///data/celery_broker.db` (Kombu transport SQLAlchemy). Suficiente para dev. Em produção LXC, o systemd injeta `QUEUE_BROKER_URL=redis://localhost:6379/0` via `EnvironmentFile=.env` e o auto-detect prefere Redis (instalado nativo via `apt`). Ver [`DEPLOY_LXC.md`](DEPLOY_LXC.md).

## Sprint 4 — entregas finais

| Item | Arquivo | Status |
|---|---|---|
| Catálogo de Erros (32 códigos) | [docs/ERROR_CATALOG.md](ERROR_CATALOG.md) | ✅ |
| Classe `AppError` + handler FastAPI | [backend/errors.py](../backend/errors.py) | ✅ |
| CLI rotate-master-key | [backend/cli/rotate_master_key.py](../backend/cli/rotate_master_key.py) | ✅ |
| `schedules.run-now` via Celery (não-bloqueante) | [backend/queue/tasks/scheduled_run_task.py](../backend/queue/tasks/scheduled_run_task.py) | ✅ |
| `A1CertSource` real (PFX + SOAP SEFAZ NFeDistribuicaoDFe) | [backend/fiscal/xml_sources/a1.py](../backend/fiscal/xml_sources/a1.py) | ✅ |
| `NfstockSource` real (REST + Bearer) | [backend/fiscal/xml_sources/nfstock.py](../backend/fiscal/xml_sources/nfstock.py) | ✅ |
| Dev local zero-config (Celery SQLite + start.py) | [scripts/start.py](../scripts/start.py), [backend/queue/celery_app.py](../backend/queue/celery_app.py) | ✅ |

## CLI rotate-master-key — uso

```powershell
# Gera chave nova automaticamente + reencripta tudo
python -m backend.cli.rotate_master_key

# Simula sem persistir
python -m backend.cli.rotate_master_key --dry-run

# Fornece a chave nova
python -m backend.cli.rotate_master_key --new-key "abc..."
```

**Sempre** faça backup de `.env` + `data/app.db` antes. O script já cria backup do `.env` mas não do banco.

## A1CertSource — configuração (Wizard ou /admin)

Settings necessários (todos no scope `api`):

| Chave | Tipo | Descrição |
|---|---|---|
| `FISCAL_A1_PFX_PATH` | string | Caminho do `.pfx` (relativo a `data/secrets/` ou absoluto) |
| `FISCAL_A1_PFX_PASSWORD` | secret | Senha do `.pfx` |
| `FISCAL_A1_CNPJ` | string | CNPJ da empresa, 14 dígitos sem máscara |
| `FISCAL_A1_UF` | string | UF de operação (`SP`, `RS`, ...) |
| `FISCAL_A1_AMBIENTE` | string | `1` produção, `2` homologação (default `1`) |
| `FISCAL_A1_TIMEOUT` | int | Timeout HTTP em segundos (default 30) |

Para ativar: `FISCAL_SOURCE=a1` no AppSetting.

## NfstockSource — configuração

| Chave | Tipo | Descrição |
|---|---|---|
| `NFSTOCK_URL` | string | URL base da API |
| `NFSTOCK_TOKEN` | secret | Bearer token |
| `NFSTOCK_TIMEOUT` | int | Timeout HTTP (default 30) |

Para ativar: `FISCAL_SOURCE=nfstock`.

## Schedules — fluxo novo

Antes (inline): `POST /api/schedules/{id}/run-now` rodava sincrono → 502 se SMTP falhasse.

Agora (fila):
1. POST `/api/schedules/{id}/run-now` → cria Job + enfileira no Celery → **202 com `job_id`** em <50ms.
2. Frontend exibe toast "enfileirado" e atualiza a lista.
3. Worker processa em background, atualiza `ScheduledReport.last_status` + `Job.status`.
4. Em falha, o Job fica em status `failed` com `error_code=ERR-JOB-005` e mensagem detalhada.

Vantagens:
- UI nunca trava em SMTP lento.
- Múltiplos agendamentos podem rodar em paralelo (worker concurrency).
- Falha grave do worker não derruba o uvicorn.

## Sprint 1–3 (resumo, já entregues anteriormente)

- **Sprint 1**: Fernet + AppSetting + Setup Wizard 6 passos + EngineRegistry pool 20+30.
- **Sprint 2**: Celery + Job model + streaming chunked (xlsx write_only / csv) + polling com backoff.
- **Sprint 3**: Auditor Fiscal real (SF1+SD1 → XML → comparators → e-mail), Dashboard remodelado (sparkline + donut + feed), Admin runtime (reload + restart + health detail).
- **Deploy**: originalmente Docker (multi-stage + docker-compose). Em 2026-05-14 o projeto migrou para **LXC bare metal (Proxmox)** via `systemd` + Nginx + Redis nativo. Guia completo em [`DEPLOY_LXC.md`](DEPLOY_LXC.md). Backup do snapshot Docker em `backup/v1.4.1-pre-lxc/` para rollback se necessário.

## Verificação end-to-end

```powershell
# 1. Smoke imports
.\.venv\Scripts\python.exe -c "from backend.main import app; print(len(app.routes))"
# Esperado: 61

# 2. Subir tudo
python scripts\start.py
# Esperado: WEB iniciado + WORKER ready em ~3s

# 3. Wizard
# Browser: http://localhost:8000/ → setup.html → completar 5 passos

# 4. Smoke do CLI rotate (dry-run, seguro)
.\.venv\Scripts\python.exe -m backend.cli.rotate_master_key --dry-run

# 5. Smoke errors
.\.venv\Scripts\python.exe -c "from backend.errors import AppError, ERR_DB_001; print(AppError(ERR_DB_001, 'x').to_dict())"
```

## O que NÃO está no escopo (Fase 4+)

- Multi-tenancy (vários clientes no mesmo container)
- WebSockets em vez de polling
- JOIN multi-tabela no Builder Visual (estrutura `joins[]` reservada)
- Power BI / dataset endpoints externos
- Distribuição NFe por NSU (A1 atual só consulta por chave)

# Sprint 11 — Enterprise UX/UI + Cron Fiscal Autônomo

**v2.3.0 · 2026-05-22 · codename `enterprise-polish`**

Cinco frentes de polimento final entregues:

| # | Frente | Status |
|---|---|---|
| 1 | XLSX formatado (verde Fertimaxi + AutoFit + ordenação por prefixo) | ✅ |
| 2a | Choices.js (busca interna) no dropdown de colunas dos filtros | ✅ |
| 2b | Botão "🧹 Limpar Formulário" no Builder | ✅ |
| 3 | Bugfix "Limpar Resultados" do Auditor (KPIs + cache + tab) | ✅ |
| 4 | Log debug agressivo no NFStock (URL, headers mascarados, response[:500]) | ✅ |
| 5 | Cron Fiscal autônomo configurável + email consolidado | ✅ |

---

## 1️⃣ Excel formatado + ordenação por prefixo

[`backend/reports.py::to_bytes`](../backend/reports.py) refatorado:

- Header com `PatternFill #2E8B3D` (verde Fertimaxi)
- Fonte branca `bold` + alinhamento `center` + `wrap_text`
- AutoFit calculado por sample (até 1000 linhas, cap 60 chars)
- `freeze_panes = "A2"` (linha 1 fixa ao rolar)
- `auto_filter` ativado automaticamente

**Ordenação inteligente** via `_reorder_columns_by_prefix(cols)`:
- JOIN format (`SF1__F1_DOC`, `SD1__D1_COD`) → agrupa por prefixo (`SF1`, `SD1`...) preservando ordem de aparição
- Single-table (`F1_DOC`, `D1_COD`) → agrupa pelos 2 primeiros chars antes do `_`

Aplicado em XLSX, CSV e ODS (PDF mantém ordem original).

## 2️⃣ Builder UX

### Choices.js (busca interna)
- CDN adicionado em [`protheus.html`](../frontend/pages/protheus.html) (`choices.js@10.2.0`)
- Cada `.f-field` do filtro vira um Choices instance com `searchEnabled: true`
- Placeholder do search: `🔎 buscar coluna…`
- Sem reordenação automática (`shouldSort: false`) — mantém ordem das tabelas
- Dark mode override no `style.css` para os elementos `.choices__inner`, `.choices__list--dropdown`

### Botão "🧹 Limpar Formulário"
- Posicionado entre "Consultar" e "Visualizar Amostra"
- Confirma antes de limpar (`confirm()`)
- Reseta TUDO: `state.module`, `state.alias`, `state.branch`, `state.columnsByAlias`,
  `state.selectedCols`, `state.filters`, `state.joins`, controles UI, grid, meta,
  paginação, botão JOIN
- Toast "Formulário limpo."

## 3️⃣ Bugfix "Limpar Resultados" do Auditor

Sprint 10 já tinha o botão, mas faltava limpar:
- ❌ Cache `sourceInfo` (carregava tolerâncias da consulta antiga)
- ❌ KPIs no topo (`kpiTotal`, `kpiCrit`, `kpiWarn`, `kpiBranches`)
- ❌ Tab ativa (operador podia ficar preso na aba "Pendentes")
- ❌ Dispatch de `input`/`change` events para listeners reagirem

Agora resolve todos:

```js
sourceInfo = null;  // invalida cache de tolerâncias
["countReal","countPending","kpiTotal","kpiCrit","kpiWarn","kpiBranches"]
  .forEach(id => $(id).textContent = "—");
// Volta para aba "Anomalias"
const tabReal = document.querySelector('[data-bs-target="#tabReal"]');
new bootstrap.Tab(tabReal).show();
// Dispatch de eventos para Choices/date-picker recalcularem
el.dispatchEvent(new Event("input", { bubbles: true }));
```

## 4️⃣ Log debug agressivo do NFStock

[`backend/fiscal/xml_sources/nfstock.py`](../backend/fiscal/xml_sources/nfstock.py):

Novo método `_debug_log_failure()` chamado em:
- Falha de rede (request nem completou)
- Status HTTP != 200
- HTML mascarado como 200 (landing page, login wall, manutenção)

Output no logger Celery (visível em `journalctl -u protheus-worker`):

```
WARNING [nfstock] [NFStock DEBUG] HTML MASCARADO COMO 200
  URL:     https://nfstock.alterdata.com.br/api/v2/Documentos/352005...0019/Xml
  Headers: {'Authorization': 'Bearer pat_AA…FFFF', 'Accept': 'application/xml,application/json'}
  Status:  200
  Preview: <!DOCTYPE html><html><body><h1>Pagina nao encontrada</h1><script src="/x.js"></script>... (truncado a 500 chars)
```

Helper `_mask_headers_for_log()` mascara `Authorization` / `User-Token` / `X-Api-Key`
preservando os **primeiros 6 + últimos 4** caracteres do token — útil para conferir
qual token está em uso sem vazar o valor inteiro.

`\n` e `\r` no response são substituídos por espaço para o log caber em uma linha
do `journalctl`.

## 5️⃣ Cron Fiscal Autônomo

### Schedule configurável
[`backend/scheduler.py`](../backend/scheduler.py) ganhou `FISCAL_SCHEDULE_OPTIONS`:

| Key | Label | Trigger |
|---|---|---|
| `disabled` | ⏸ Desativado | — |
| `every-3h` | 🕐 A cada 3 horas | `IntervalTrigger(hours=3)` |
| `every-6h` | 🕓 A cada 6 horas | `IntervalTrigger(hours=6)` |
| `every-12h` | 🕛 A cada 12 horas | `IntervalTrigger(hours=12)` |
| `daily-6h` | 🌅 Diariamente às 06:00 | `CronTrigger(hour=6)` |
| `daily-18h` | 🌇 Diariamente às 18:00 | `CronTrigger(hour=18)` |
| `weekdays-6h` | 🗓 Dias úteis às 06:00 (default) | `CronTrigger(day_of_week="mon-fri", hour=6)` |

Função `reload_fiscal_schedule()`:
- Lê `AppSetting('FISCAL_AUTO_SCHEDULE')`
- Remove job `fiscal_tick` antigo + adiciona novo com o trigger atualizado
- `misfire_grace_time=300` (5 min de tolerância se o worker estava down na hora certa)
- Chamada por `start()` no boot + endpoint admin pós-mudança

### Email consolidado em modo autônomo
`run_audit(autonomous_mode=True)` muda o comportamento do email:
- **Manual** (botão "+ Nova auditoria"): só envia se `anomalies > 0` (comportamento antigo)
- **Autônomo** (cron): SEMPRE envia. Se zero anomalias → assunto `[Auditor Fiscal] Auditoria OK em DD/MM/YYYY`. Se ≥1 → `[Auditor Fiscal AUTO] N divergências em ...`

`fiscal_tick` agora marca `payload["autonomous_mode"] = True`. `fiscal_task` lê e propaga.

### UI Admin
Nova seção em **Configurações > Auditor Fiscal**:

```
🤖 Auditoria Automática (cron)
[ Dropdown frequência ]                   [ Salvar frequência ]
ℹ️ Roda nas filiais marcadas em "Filiais...". 
   E-mail enviado mesmo se 0 anomalias.    próxima: 22/05/2026 18:00:00
```

Endpoints novos:
- `GET  /api/admin/config/fiscal-schedule` → `{options, current, label, next_run_time, enabled}`
- `POST /api/admin/config/fiscal-schedule` → body `{schedule: "every-6h"}` → reagenda o job em tempo real

## Smoke test

```
[1] XLSX format + reorder OK (fill=FF2E8B3D, bold, center, freeze A2)
[5] Schedule options: ['disabled','every-3h','every-6h','every-12h','daily-6h','daily-18h','weekdays-6h']
[5] _build_fiscal_trigger OK (None para 'disabled', trigger para outros)
[5] Endpoints /api/admin/config/fiscal-schedule (GET + POST) registrados
[4] NFStock mask: 'Bearer pat_AA…FFFF' (primeiros 6 + ultimos 4)

JS sanity:
  protheus.js: 347/347 braces
  admin.js:    280/280 braces
  fiscal.js:   215/215 braces

Security audit: 0 CRITICAL · 0 HIGH · 0 MEDIUM → CONFORMIDADE
```

## Como validar em produção

### 1. Excel formatado
Builder → consulta com JOINs → "📥 Baixar Excel" → abre no Excel:
- Linha 1 verde Fertimaxi + branco bold + centralizado
- Cabeçalho fixo ao rolar (freeze)
- Auto-filter ativo (combo na linha 1)
- Colunas agrupadas: todas as SF1_* juntas, depois SD1_*, etc

### 2. Choices.js
Builder → adicionar filtro → click no dropdown de coluna → barra de busca aparece
no topo, digite "EMISSAO" → lista filtra em tempo real

### 3. Limpar Formulário
Builder com modulo + tabela + 2 JOINs + 5 colunas + 3 filtros → "🧹 Limpar Formulário"
→ confirma → tela volta ao estado inicial (modulo vazio, sem alias, etc)

### 4. Limpar Resultados Auditor
Auditor → "Filtrar" com período antigo → "🧹 Limpar Resultados" → KPIs voltam para "—",
contadores das abas zerados, aba "Anomalias" ativa, nova "Filtrar" não traz dados antigos

### 5. NFStock debug
Sem mudar configuração, rode auditoria. Se algum XML pendente:
`journalctl -u protheus-worker -f | grep "NFStock DEBUG"` mostra URL + headers + response

### 6. Cron Fiscal
Admin → Configurações > Auditor Fiscal → "Auditoria Automática" → escolha "A cada 3 horas"
→ "Salvar frequência" → toast verde + "próxima: 22/05/2026 21:00:00".
Mude para "Desativado" → toast e "próxima: — (desativada)".

## ⏭️ Pós Sprint 11

- Adicionar mais opções de cron (semanal/quinzenal)
- Endpoint `POST /api/admin/fiscal/run-now` para disparar manualmente uma rodada
  autônoma (útil para validar configuração)
- Persistir histórico das rodadas autônomas em uma tabela `fiscal_auto_runs`
  com link para o `job_id` correspondente
- Choices.js também no select de colunas dos JOINs (atualmente só nos filtros)

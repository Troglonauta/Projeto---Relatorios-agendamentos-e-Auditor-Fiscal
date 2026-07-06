# Sprint 8 — Parte 3: Dashboards Visuais + Webhooks + LGPD (2026-05-20)

**Code Freeze v2.0** — três frentes de gestão/compliance:

| # | Frente | Status | Onde |
|---|---|---|---|
| 1 | **Dashboard com Chart.js** + Dark mode awareness | ✅ | `dashboard.js` |
| 2 | **Webhook de alertas** (Slack/Teams/genérico) | ✅ | `fiscal/webhook.py` |
| 3 | **LGPD Data Masking** dinâmico por role | ✅ | `lgpd.py` |

---

## 1️⃣ Dashboards Visuais (Chart.js)

### O que já existia (Sprint 8 anterior)
- Sparkline (linha) — relatórios concluídos 7d
- Doughnut "Auditorias recentes" (OK/Anomalia/Outros — por job outcome)
- Stacked bars 30d — anomalias por dia/severidade

### O que mudou (Sprint 8 Part 3)

**Novo doughnut "Anomalias por Severidade"** — diferente do existente (que era
por job outcome). Esse novo agrega TODAS as anomalias dos últimos 30 dias
pelo campo `severity`:

| Fatia | Cor |
|---|---|
| Crítica | `#C0392B` |
| Aviso | `#F2C037` |
| Info | `#2E8B3D` |
| Pendente (XML não encontrado) | `#9aa5b1` |

Tooltip custom mostra `{label}: {N} ({pct}%)`. Subtítulo dinâmico:
`N no total · X crítica(s) (Y%)`. Endpoint backend: `/api/fiscal/summary?days=30`
(o existente, com `pending` adicionado).

**Dark Mode awareness em TODAS as charts**:

```js
const _chartRegistry = new Map();     // id → instância
const _chartFactories = new Map();    // id → factory()

function _renderChart(id, factory) {
  const prev = _chartRegistry.get(id);
  if (prev) prev.destroy();
  _applyThemeDefaults();              // muda Chart.defaults.color
  _chartRegistry.set(id, factory());
  _chartFactories.set(id, factory);
}

document.addEventListener("theme:changed", () => {
  for (const [id, factory] of _chartFactories) {
    _chartRegistry.get(id)?.destroy();
    _applyThemeDefaults();
    _chartRegistry.set(id, factory());
  }
});
```

`_themeColors()` lê as CSS variables (`--text`, `--muted`, `--border`)
diretamente via `getComputedStyle(document.body)`. Quando o usuário troca
o tema, `theme.js` dispara `CustomEvent("theme:changed")` — todos os
4 charts são destruídos e recriados com cores do tema atual em < 100ms.

Grid lines mais sutis no dark (`rgba(255,255,255,0.06)` vs `rgba(0,0,0,0.06)`).

---

## 2️⃣ Webhook de Alertas Críticos

### Arquitetura
- Setting `FISCAL_WEBHOOK_URL` armazenada **encriptada** (Fernet via
  `is_secret=True`). A URL contém o token na própria path
  (`https://hooks.slack.com/services/T01/B01/ABC123`), tratamos como segredo.
- Após `run_audit` no [`fiscal_task.py`](../backend/queue/tasks/fiscal_task.py),
  se `stats["critical"] > 0` E URL configurada → dispara POST.
- Best-effort: erro de rede / 4xx / 5xx é **apenas logado** — auditoria já
  terminou com sucesso, webhook é secundário.

### Payload enviado

Compatível com Slack incoming webhook + Microsoft Teams incoming webhook
+ webhooks genéricos (Discord, n8n, custom):

```json
{
  "text": "🚨 *Alerta Fiscal — 3 divergencia(s) CRITICA(s)* encontrada(s) na auditoria de *01/01/26 a 30/01/26*.\n• 2 divergencia(s) de NCM (risco SPED).\n• Total de anomalias: 12 · 5 XML(s) pendente(s) na fonte\n📊 Confira o painel: Auditor Fiscal > Anomalias",
  "severity": "critical",
  "stats": {
    "period": "01/01/26 a 30/01/26",
    "total": 12,
    "critical": 3,
    "ncm": 2,
    "pending": 5,
    "branches": ["01", "02"]
  }
}
```

Timeout HTTP 8s. Sem retry automático (se cair, próxima auditoria tenta de novo).

### UI Admin

Bloco novo na aba **Configurações > Auditor Fiscal**:

```
🚨 Webhook de Alertas
─────────────────────────────────────────────
URL do Webhook (vazio = desativa alertas)
[ https://hooks.slack.com/services/...     ]
✓ Webhook configurado: https://hooks.slack.com/services/T01/B01/***

[ 🧪 Enviar mensagem de teste ]    [ Salvar Webhook ]
```

- **Salvar**: `POST /api/admin/config/webhook` com body `{url}`. URL vazia desativa.
- **Testar**: `POST /api/admin/test/webhook` envia uma mensagem dummy
  (`text: "Teste de webhook do Protheus Reports..."`). Resposta:
  `{ok, status_code, detail}`. Não consome quota — só valida que a URL aceita.
- Frontend exibe **URL truncada** (`/services/T01/B01/***`) para não vazar
  o token visualmente na tela.

### Smoke test
```python
webhook._build_message({"critical":3, "anomalies":7, "ncm_divergences":2, "period":"..."})
→ severity="critical"
→ text contém "Alerta Fiscal" + "3" + "2"
→ stats inclui {total, critical, ncm, pending, branches}
```

---

## 3️⃣ LGPD — Mascaramento Dinâmico de Campos Sensíveis

### Catálogo de colunas sensíveis

Novo módulo [`backend/lgpd.py`](../backend/lgpd.py) com mapa de colunas
Protheus → tipo de máscara:

| Coluna | Tipo | Mascara |
|---|---|---|
| `A1_CGC`, `A2_CGC`, `F1_CGCFOR`, `F2_CGCDEST` | `doc_partial` | `12.***.***/****-99` |
| `RA_CIC` (CPF RH) | `doc_partial` | `12.***.***-99` |
| `A1_INSCR`, `A2_INSCR`, `A1_INSCRM`, `A2_INSCRM` | `full` | `*** LGPD ***` |
| `RA_RG`, `A1_RG`, `A2_RG` | `full` | `*** LGPD ***` |
| `RA_SALARIO`, `RA_VALORHORA`, `RA_HRSEMAN` | `full` | `*** LGPD ***` |
| `A1_PESSOAL`, `A2_PESSOAL` | `full` | `*** LGPD ***` |

**Regra de máscara `doc_partial`** (CNPJ/CPF):
- Mantém **2 primeiros + 2 últimos** dígitos
- Permite reconciliação parcial (operador cruza com extrato sem ver doc completo)
- Strings com < 5 dígitos viram `*** LGPD ***`

### Detecção de coluna

Suporta os 2 formatos de saída do Builder:
- **Single**: `A1_CGC` → match direto
- **JOIN qualificado** (Sprint 5): `SA1__A1_CGC` → extrai suffix `A1_CGC`

```python
def _column_suffix(col_name):
    return col_name.split("__", 1)[1].upper() if "__" in col_name else col_name.upper()
```

### Aplicação por role

```python
def should_mask_for(user) -> bool:
    """True quando user NAO e' admin. Operador comum SEMPRE recebe mascarado."""
    role = getattr(user, "role", None) or ...
    return str(role).lower() != "admin"
```

**Admin vê dados crus** (finalização contábil/fiscal). **Operador vê
mascarado** (compliance LGPD art. 5).

### Pontos de injeção

| Endpoint | Como | Onde |
|---|---|---|
| `POST /api/protheus/query` (JSON, single+JOIN) | `lgpd.apply_to_rows()` antes do return | [protheus_routes.py](../backend/routers/protheus_routes.py) |
| `POST /api/protheus/download` (XLSX/CSV/PDF sync) | `lgpd.apply_to_rows()` antes de `reports.to_bytes()` | [protheus_routes.py](../backend/routers/protheus_routes.py) |
| Celery `report_task.generate_report` (XLSX/CSV streaming) | `lgpd.wrap_row_iterator()` no iterator | [report_task.py](../backend/queue/tasks/report_task.py) |

Para o caso streaming: o wrapper **espia a 1ª linha**, calcula o `mask_map`
uma vez, e aplica O(1) por linha — preserva o O(1) de memória do
`write_xlsx_stream`.

### Smoke test
```
CNPJ '12345678000199' → '12.***.***/****-99'
CPF  '98765432100'    → '98.***.***-00'
'123' (curto)         → '*** LGPD ***'
None                  → None  (preserva nulos)

mask_map(['SA1__A1_CGC','SA1__A1_NOME','SC5__C5_NUM','RA_SALARIO'])
  → {'SA1__A1_CGC':'doc_partial', 'RA_SALARIO':'full'}

apply_to_rows(rows, user=operator):
  [{'SA1__A1_CGC':'12.***.***/****-99', 'SA1__A1_NOME':'ACME',
    'SC5__C5_NUM':'1', 'RA_SALARIO':'*** LGPD ***'}]

should_mask_for(admin)    → False
should_mask_for(operator) → True
should_mask_for(None)     → True (fail-safe)
```

### Segurança verificada
`scripts/security_check.py` ganhou `FISCAL_WEBHOOK_URL` em
`MUST_BE_ENCRYPTED`. Audit:
```
CRITICAL=0  HIGH=0  MEDIUM=0  →  AMBIENTE EM CONFORMIDADE
```

---

## ✅ Smoke tests consolidados

```
Total rotas: 104 (2 novas: /api/admin/config/webhook, /api/admin/test/webhook)
Endpoints novos:
  POST /api/admin/config/webhook   → salva URL (Fernet)
  POST /api/admin/test/webhook     → dispara mensagem dummy

LGPD module:
  ✓ Catalogo de 17 colunas sensiveis Protheus
  ✓ Suporta JOIN qualified (SA1__A1_CGC) e single (A1_CGC)
  ✓ doc_partial mantem 2+2 digitos para reconciliacao
  ✓ wrap_row_iterator preserva streaming O(1)
  ✓ should_mask_for(None) = True (fail-safe)

Webhook:
  ✓ _build_message gera payload Slack/Teams compatible
  ✓ severity="critical" quando stats.critical > 0
  ✓ stats embutidas para webhooks genericos
  ✓ Timeout 8s, sem retry, best-effort no fiscal_task

Charts:
  ✓ Novo chartSeverity (doughnut por severidade — usa /api/fiscal/summary)
  ✓ 4 charts theme-aware via _renderChart wrapper
  ✓ theme:changed event re-renderiza tudo automaticamente
  ✓ Grid lines mais sutis no dark mode

Security audit: 0 CRITICAL · 0 HIGH · 0 MEDIUM
JS sanity:
  dashboard.js: 100/100 braces, 149/149 parens
  admin.js: 251/251 braces, 584/584 parens
```

---

## 🧪 Como validar em produção

### Charts
1. Dashboard → 4 cards visuais (sparkline + doughnut fiscal + **novo doughnut
   severidade** + stacked bars 30d).
2. Toggle **🌙 Modo escuro** no sidebar → as 4 charts re-renderizam com
   eixos brancos + grid sutil. Toggle de volta → eixos pretos.
3. Hover na nova doughnut → tooltip mostra `{Crítica: 3 (25.0%)}`.

### Webhook
1. Admin → Auditor Fiscal → cole URL Slack `https://hooks.slack.com/...`
   → "Salvar Webhook" → toast verde.
2. "🧪 Enviar mensagem de teste" → Slack recebe "🧪 Teste de webhook..."
   em segundos. Toast no painel: "✅ Webhook respondeu HTTP 200".
3. Rode auditoria com pelo menos 1 anomalia crítica → ao final, Slack
   recebe "🚨 *Alerta Fiscal — N divergencia(s) CRITICA(s)*..." com stats.
4. Desconfigure (URL vazia + Salvar) → próximas auditorias NÃO disparam alerta.

### LGPD
1. Login como **operador** (não-admin) → Builder → SA1 + colunas
   `A1_NOME, A1_CGC, A1_INSCR`.
2. **Consultar** → tabela mostra `A1_CGC` como `12.***.***/****-99` e
   `A1_INSCR` como `*** LGPD ***`. `A1_NOME` aparece normal.
3. **Baixar Excel (Rápido)** → planilha tem os mesmos valores mascarados.
4. **Gerar em Background** (Celery) → XLSX baixado também mascara.
5. Login como **admin** → mesma consulta → todos os campos crus visíveis.
6. Audit log mostra `protheus.query`/`protheus.download` com o user que
   pediu — rastreabilidade preservada.

---

## ⏭️ Pós Code Freeze v2.0

- **Adicionar mais colunas no catálogo LGPD**: revisar com fiscal/RH para
  endereços (`A1_END`), telefones pessoais, etc.
- **Logging quando user nao-admin baixa colunas sensíveis** (mesmo
  mascaradas — auditoria fina). Hoje o audit_log já registra a query mas
  não destaca que houve campos sensíveis.
- **Webhook por filial**: hoje 1 URL global. Permitir `FISCAL_WEBHOOK_URL_01`,
  `FISCAL_WEBHOOK_URL_02` para times distribuídos.
- **Chart "anomalias por filial"** — barra horizontal com top-5 filiais por
  contagem crítica. Útil para Controladoria que cobre 8 filiais Fertimaxi.

# Sprint 4.C — UX do Auditor + bugfixes (2026-05-15)

## 🐛 Bugfixes

### 1. `'TextClause' object has no attribute 'bindparam'`

Erro disparado em `_exec_in_cols`, `_exec_in` e `_exec_chvnfe` quebrava o
batch loading das tabelas auxiliares (SDT, SE2, SFT, SF3, SB1).

**Causa**: usei `text("vals").bindparam(value=..., expanding=True)` — `text("vals")`
cria um `TextClause` com o texto literal `"vals"`, e `TextClause` não tem método
`.bindparam()` (singular). O correto é importar **`bindparam` global** do `sqlalchemy`.

**Padrão correto** (aplicado nos 3 helpers em [auditor.py](../backend/fiscal/auditor.py)):
```python
from sqlalchemy import bindparam, text

stmt = text("SELECT ... WHERE col IN :vals").bindparams(
    bindparam("vals", expanding=True)
)
conn.execute(stmt, {"vals": list(values)})
```

Smoke test passou contra SQLite com lista `['a', 'b', 'c']` → retornou `['a', 'c']`.
Em SQL Server o comportamento é idêntico.

### 2. Banner mostrando "Fonte ativa: TOTVS TSS" com Transmite credenciado

Instalações pré-Fase 4.B ficaram com `FISCAL_SOURCE=tss` (default antigo) no SQLite,
mesmo após preencher credenciais Transmite no Wizard. O auto-set do Wizard só
roda em instalações novas.

**Correções:**
- **`_migrate_fiscal_source_to_transmite()`** no lifespan de [main.py](../backend/main.py):
  promove `FISCAL_SOURCE='transmite'` quando as 3 credenciais Transmite estão
  preenchidas E a fonte atual (tss/a1) não tem credenciais próprias.
  Idempotente — só altera se realmente precisar.
- **Banner inteligente** em [fiscal.js](../frontend/js/fiscal.js): quando a fonte
  ativa não está configurada mas outra está, mostra botão amarelo
  *"Trocar para TOTVS Transmite"* (ou TSS/NFSTOCK conforme caso) que chama
  o novo endpoint `POST /api/fiscal/source/switch`.

---

## 🆕 Sprint 4.C — 3 entregas + bonus

### 1. Modal de Detalhes da Anomalia (side-by-side)

[fiscal.js::openDetailModal](../frontend/js/fiscal.js) — modal Bootstrap XL com:
- Cabeçalho: chave NFe, filial, CNPJ, campo, severidade, tolerância aplicada.
- Banner vermelho 🚨 quando é NCM (compliance fiscal).
- **Comparação side-by-side em 2 colunas**: 📦 Protheus vs 📄 XML (SEFAZ).
  - Ambas em `<pre>` com `white-space: pre-wrap` para preservar formatação.
  - Card verde claro (Protheus) e azul claro (XML).
- Banner contextual mostrando ack/snooze atual quando aplicável.

CSS novo em [style.css](../frontend/css/style.css):
`.anomaly-side`, `.anomaly-side-title`, `.anomaly-side-xml`, `.anomaly-value`.

Cada linha da lista vira `clickable` — hover destaca com verde Fertimaxi.

### 2. Gestão de Status (Ack / Snooze / Reabrir)

**Backend** — model `FiscalAnomaly` ganhou 4 campos novos ([models.py](../backend/models.py)):

| Campo | Tipo | Uso |
|---|---|---|
| `acknowledged_at` | DateTime, index | "Ciente" definitivo — sai do Dashboard |
| `acknowledged_by_id` | FK users | Quem deu o ack |
| `ack_note` | Text | Justificativa opcional |
| `snoozed_until` | DateTime, index | Silenciada até essa data |

**Endpoints** ([fiscal_routes.py](../backend/routers/fiscal_routes.py)):
- `POST /api/fiscal/anomaly/{id}/ack` — body `{note?, snooze_days?}`:
  - sem `snooze_days` → ack definitivo
  - com `snooze_days` (1..365) → snooze por N dias
- `POST /api/fiscal/anomaly/{id}/unack` — reverte (volta ao Dashboard).

**Filtros automáticos** — Sprint 4.C ajustou 3 endpoints para ignorar anomalias
ack/snoozed por padrão:

| Endpoint | Comportamento |
|---|---|
| `GET /api/fiscal/anomalies` | Aceita `?include_acked=true` para ver todas |
| `GET /api/fiscal/summary` | KPIs ignoram ack/snoozed; campo novo `acked` mostra contagem |
| `GET /api/dashboard/feed` | Anomalias ack/snoozed não entram no feed |
| `GET /api/fiscal/anomalies/export` | Aceita `?include_acked=true` |

**Frontend** — no modal de detalhes:
- Quando anomalia está **aberta**: input "Nota (opcional)" + botão dropdown
  *⏰ Snooze* (1/7/30 dias) + botão verde *✓ Marcar como ciente*.
- Quando já está ack/snooze: botão amarelo *↶ Reabrir*.
- Linhas ack/snoozed na lista ficam atenuadas (opacity 0.55 + itálico).
- Badge ao lado do campo: `✓ ciente` ou `⏰ snooze`.

Switch *"Incluir anomalias já cientes / em snooze"* nos filtros da lista.

### 3. Exportação CSV / XLSX

Novo endpoint **`GET /api/fiscal/anomalies/export?fmt=csv|xlsx`** que aplica
os mesmos filtros (date_from/to, severity, branch, ncm_only, include_acked) e
retorna `StreamingResponse`.

- **CSV**: separador `;` (padrão BR), BOM UTF-8 para Excel abrir sem caracter quebrado.
- **XLSX**: `openpyxl.Workbook(write_only=True)` — handler streaming sem RAM extra.

Colunas exportadas:
```
id | auditado_em | chave_nfe | filial | cnpj_fornecedor | campo |
valor_protheus | valor_xml | severidade | ack_em | snooze_ate | ack_nota
```

Frontend [fiscal.js](../frontend/js/fiscal.js) — botão dropdown verde no canto direito
dos filtros, com 2 opções (`CSV (separador ;)` e `Excel (.xlsx)`). Download
automático com nome `anomalias_YYYY-MM-DD.{csv|xlsx}`.

### Bônus: troca de fonte ativa pelo banner

`POST /api/fiscal/source/switch` body `{"source": "transmite|tss|a1|nfstock"}` —
admin troca a fonte ativa sem precisar ir até **Configurações > Auditor Fiscal**.

---

## Smoke test passou

```
bindparam SQLite: ['a', 'c']                  ✓ correção bindparam funcional
Ack/snooze cols presentes: True               ✓ 4 colunas novas em FiscalAnomaly
Endpoints Sprint 4.C:
 - /api/fiscal/anomalies/export
 - /api/fiscal/anomaly/{anomaly_id}/ack
 - /api/fiscal/anomaly/{anomaly_id}/unack
 - /api/fiscal/source/switch
```

## Backup

`backup/v1.4.3-pre-sprint-4C/snapshot.tar.gz` (165 KB) — estado pré Sprint 4.C
para rollback se necessário.

## Como testar

1. **Bugfix bindparam**: rode `python scripts/start.py` e dispare uma auditoria.
   Os warnings `'TextClause' object has no attribute 'bindparam'` devem ter sumido
   do log; o worker agora processa SDT/SE2/SFT/SF3/SB1 normalmente.

2. **Migração FISCAL_SOURCE**: ao subir, o log mostra:
   ```
   Migracao FISCAL_SOURCE: 'tss' -> 'transmite' (credenciais Transmite preenchidas
   e fonte anterior sem credenciais).
   ```
   No banner do Auditor Fiscal a fonte deve aparecer agora como TOTVS Transmite.

3. **Modal de detalhes**: vá em Auditor Fiscal → clique numa linha → veja o
   side-by-side Protheus × XML. Para NCM, banner vermelho aparece no topo.

4. **Ack/Snooze**: no modal de detalhes, marque como ciente (com nota). Depois
   olhe o Dashboard: a anomalia some do feed e dos KPIs. Para reabrir, marque
   "Incluir anomalias já cientes" no filtro e clique de novo.

5. **Export**: filtre algumas anomalias → botão *📥 Exportar lista* →
   `CSV (separador ;)` ou `Excel`. Abra no Excel e confirme acentuação OK.

## Próximas sugestões

- Histórico de ack: tabela `fiscal_anomaly_status_log` para rastrear quem ack/snooze e quando.
- Re-abertura automática: se a mesma divergência aparecer em nova auditoria depois
  de ack, ressuscitar (overlay de `acknowledged_at`).
- Notificação Slack/Teams além de e-mail (webhook).

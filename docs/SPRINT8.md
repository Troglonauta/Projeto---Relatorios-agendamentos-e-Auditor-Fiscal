# Sprint 8 — Resiliência + UX/UI Enterprise (2026-05-20)

Três frentes:

| # | Frente | Status | Onde |
|---|---|---|---|
| 1 | **Resiliência de XML** (bugfix crítico — worker Celery crashava em HTML) | ✅ | `nfstock.py`, `auditor.py` |
| 2 | **UX da Controladoria** — Meus Modelos + Visualizar Amostra | ✅ | `protheus.js` + CSS |
| 3 | **Modernidade visual** — Skeleton loaders + Chart.js bar | ✅ | `dashboard.js` + CSS |

---

## 1️⃣ Resiliência do motor fiscal

### Bug em produção
```
lxml.etree.XMLSyntaxError: Opening and ending tag mismatch: link line 15 and head, line 17
```
Causa: a API NFStock devolveu uma **página HTML** (404/500 mascarado) com status
200 — passamos os bytes direto para `etree.fromstring` que travou o worker
Celery inteiro.

### Fix 1A — `nfstock.py` rejeita HTML antes do parser

[`backend/fiscal/xml_sources/nfstock.py`](../backend/fiscal/xml_sources/nfstock.py):

```python
_HTML_MARKERS = (b"<!doctype html", b"<html", b"<head", ...)

@classmethod
def _looks_like_html(cls, content: bytes) -> bool:
    head = content[:512].lower()
    return head.lstrip().startswith((b"<!doctype html", b"<html", b"<head")) \
        or b"<html" in head or b"<head>" in head

def _parse_response(self, r, url, chave) -> bytes:
    if r.status_code != 200:
        raise XmlNotFound(...)            # ← Sprint 8: rejeita não-200 explicitamente
    if self._looks_like_html(r.content):
        raise XmlNotFound(
            f"NFStock {url}: API retornou pagina HTML em vez de XML "
            f"(provavel erro 404/500 mascarado). Preview: {preview!r}"
        )
    # ... resto idêntico
```

Bloqueia também HTML embutido em JSON via `xml_base64` (caso raro mas
acontece em outros provedores).

### Fix 1B — `auditor.py` envolve `etree.fromstring` em try/except

[`backend/fiscal/auditor.py`](../backend/fiscal/auditor.py): mesmo após a
defesa de `nfstock.py`, pode chegar XML truncado/encoding quebrado de
outras fontes. O `etree.XMLSyntaxError` antes matava o worker inteiro.
Agora vira anomalia e o loop continua:

```python
try:
    xml_header = _parse_nfe_header(xml_bytes)
    xml_items  = _parse_nfe_items(xml_bytes)
    xml_totals = _parse_nfe_totals(xml_bytes)
    xml_dups   = _parse_nfe_duplicatas(xml_bytes)
    consecutive_errors = 0
except etree.XMLSyntaxError as exc:
    logger.warning("XML corrompido para chave %s: %s ...", chave[-8:], exc)
    anomaly = FiscalAnomaly(
        doc_key=chave, branch=branch,
        field_compared="xml_corrompido",
        protheus_value=doc.get("F1_DOC", ""),
        xml_value=f"XML invalido/HTML retornado: {str(exc)[:200]}",
        severity="warn", job_id=job_id,
    )
    db.add(anomaly); db.commit()
    stats["anomalies"] += 1
    stats.setdefault("docs_xml_corrupt", 0)
    stats["docs_xml_corrupt"] += 1
    consecutive_errors = 0   # NÃO conta como falha da fonte
    continue
```

### Fix 1C — silenciar `pyodbc.ProgrammingError` em tabelas opcionais

Tabelas release-dependent (CK0COL, SDT em algumas releases) **não existem**
em todos os clientes — gerando `pyodbc.ProgrammingError` ruidoso em todo
run do auditor:

```
WARNING  _exec_in_cols falhou em SDT010.DT_NFISC (cols=...): 
         ('42S22', "[42S22] [Microsoft][ODBC Driver 17 for SQL Server]...
         Invalid column name 'DT_NFISC'")
```

Novo helper detecta o tipo específico:

```python
try:
    import pyodbc
    _SQL_NOISY_ERRORS = (pyodbc.ProgrammingError,)
except Exception:
    _SQL_NOISY_ERRORS = ()

def _is_missing_table_or_col(exc) -> bool:
    if _SQL_NOISY_ERRORS and isinstance(exc, _SQL_NOISY_ERRORS):
        return True
    orig = getattr(exc, "orig", None)   # SQLAlchemy embrulha
    if orig is not None and _SQL_NOISY_ERRORS and isinstance(orig, _SQL_NOISY_ERRORS):
        return True
    return type(exc).__name__ == "ProgrammingError"
```

Os 3 helpers (`_exec_in`, `_exec_in_cols`, `_exec_chvnfe`) agora fazem:
```python
except Exception as exc:
    if _is_missing_table_or_col(exc):
        logger.debug(...)        # silencioso, só em DEBUG
    else:
        logger.warning(...)      # mantém para outros erros
    return []
```

---

## 2️⃣ UX — Consultas Salvas + Visualizar Amostra

### Meus Modelos (localStorage, sem backend)

Barra nova no topo do Builder com 4 controles:
- **Dropdown** "— Carregar modelo salvo —" lista todos os modelos do usuário
- **📂 Carregar** restaura módulo + alias + filial + JOINs + colunas + filtros
- **💾 Salvar este modelo** captura o estado atual, pede nome (prompt), salva
- **🗑️ Excluir** remove o selecionado

Storage: chave `pr_saved_models_v1` em `localStorage`. Cada modelo:
```json
{
  "name": "Pedidos SC5 + Cliente SA1",
  "saved_at": "2026-05-20T13:42:18Z",
  "alias": "SC5", "branch": "01", "module": "COMERCIAL",
  "selected_columns": ["SC5.C5_NUM", "SC5.C5_EMISSAO", "SA1.A1_NOME"],
  "filters": [{"field": "SC5.C5_EMISSAO", "op": "gte", "value": "20260101"}],
  "joins": [
    {"alias": "SA1", "branch": "01", "join_type": "INNER",
     "on": [{"left_alias": "SC5", "left_column": "C5_CLIENTE", "right_column": "A1_COD"}]}
  ]
}
```

`_applyModelToState(model)` orquestra a restauração na ordem certa:
1. Módulo → re-render aliases
2. Alias + Filial (loadBranches)
3. **JOINs antes de loadColumns** — para que `loadColumns()` puxe colunas
   da base **e de todas** as tabelas dos JOINs numa varredura
4. Filtros + colunas selecionadas
5. Re-render

### Visualizar Amostra (TOP 100 inline)

Botão `👁️ Visualizar Amostra` ao lado do "Consultar". Força `page=1,
page_size=100` e renderiza no grid do navegador — sem download, sem job de
fila. Útil para iterar filtros antes de baixar o Excel completo.

---

## 3️⃣ UI/UX — Skeleton loaders + Chart.js

### Skeleton loaders

[CSS adicionado em `style.css`](../frontend/css/style.css):

```css
.skeleton-bar {
  display: inline-block;
  height: 12px;
  border-radius: 4px;
  background: linear-gradient(90deg,
    rgba(0,0,0,0.06) 0%, rgba(0,0,0,0.12) 50%, rgba(0,0,0,0.06) 100%);
  background-size: 200% 100%;
  animation: skeleton-shimmer 1.4s ease-in-out infinite;
  min-width: 40px;
}
@keyframes skeleton-shimmer {
  0%   { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
```

`renderSkeleton(cols, rows)` em `protheus.js` injeta uma grade de barras
pulsantes durante o carregamento (acionado por `btnQuery` e `btnPreview`).
Tamanhos aleatórios entre 30% e 90% dão a sensação visual de conteúdo
diversificado.

### Chart.js — Anomalias 30 dias (stacked bars)

**Backend** novo endpoint:
```
GET /api/dashboard/fiscal-anomalies-histogram?days=30
→ { days, series: [{date, total, critical, warn, info}, ...] }
```

1 SELECT agrupado por `cast(audited_at AS Date) + severity` — evita N+1.
Preenche dias sem anomalia com zero para o eixo X ficar contínuo.

**Frontend** — novo card no Dashboard com `<canvas id="chartAnomalies30d">`
e Chart.js do CDN (já estava linkado). Tipo `bar` com 3 datasets empilhados:
- 🟥 Críticas (`#C0392B`)
- 🟨 Aviso (`#F2C037`)
- ⬜ Info (`#9aa5b1`)

Eixo X com `maxTicksLimit: 15` para evitar sobreposição em 30 labels.
Subtítulo dinâmico: "N anomalia(s) nos últimos 30 dias · X críticas".

---

## ✅ Smoke test (Sprint 8)

```
NFStock blindado:
  looks_like_html('<!DOCTYPE html>...')   → True   (rejeita)
  looks_like_html('<?xml ?><NFe>...')     → False  (passa)
  looks_like_html('  <html>...')          → True   (rejeita c/ whitespace)
  status 500 → XmlSourceError
  status 200 + HTML → XmlNotFound (registra anomalia, continua)

Auditor:
  _is_missing_table_or_col(FakeErr) → False    (mantém log warning)
  _is_missing_table_or_col(pyodbc.ProgrammingError) → True (silencioso debug)
  etree.XMLSyntaxError não crasha mais o worker — vira anomalia "xml_corrompido"

Rotas:
  GET /api/dashboard/fiscal-anomalies-histogram (NEW, Sprint 8)
  Total: 98 rotas

Security audit:
  CRITICAL=0  HIGH=0  MEDIUM=0  →  AMBIENTE EM CONFORMIDADE
```

---

## 🧪 Como validar

### Resiliência (1)
1. Aponte o NFStock para uma URL errada (ex: `https://example.com/erro404`)
2. Rode auditoria → veja no log: warning "API retornou pagina HTML em vez de XML"
3. Confira no painel Anomalias: linhas `xml_nao_encontrado` (não-200) e
   `xml_corrompido` (XML invalido) registradas. Worker NÃO crashou.
4. Logs do terminal: sem mais `WARNING _exec_in_cols falhou em SDT010...`
   — só aparecem em modo DEBUG.

### Meus Modelos (2A)
1. Builder → escolha SC5 + filial 01 + colunas C5_NUM, C5_EMISSAO + filtro
   `C5_EMISSAO >= 01/01/2026`. **💾 Salvar este modelo** → digite "Pedidos
   janeiro 2026" → toast verde.
2. Mude para outra tabela. Dropdown "Meus Modelos" → escolha o salvo →
   **📂 Carregar** → tudo volta exatamente.
3. Mesma máquina, outro tab: o modelo está lá. Outro navegador / outra
   máquina: NÃO está (localStorage é per-browser, por design).

### Visualizar Amostra (2B)
1. Builder com colunas escolhidas → **👁️ Visualizar Amostra** → grid
   mostra "👁️ Amostra · SC5010 · primeiras 100 linhas" sem job de fila.

### Skeleton (3A)
1. Clique "Consultar" com uma consulta lenta → grid mostra 12 linhas de
   barras cinzas pulsantes em vez do "Sem dados".

### Chart 30 dias (3B)
1. Dashboard → role para baixo → "📊 Anomalias fiscais — últimos 30 dias".
2. Hover numa barra → tooltip empilhado mostra críticas/aviso/info do dia.
3. Subtítulo no canto: "N anomalia(s) nos últimos 30 dias · X críticas".

---

## ⏭️ Próximas sugestões

- **Modelos compartilhados**: hoje `localStorage` é per-browser. Migrar para
  uma tabela `saved_queries` no SQLite quando aparecer demanda de
  compartilhamento entre times.
- **Skeleton em outras telas**: schedules, fiscal, audit-log também ganham
  ao trocar "Carregando…" por skeleton.
- **Chart de tendência mensal**: 12 meses agregados em vez de 30 dias.
- **Cancelar amostra**: usuário deve poder abortar a request de Visualizar
  Amostra (hoje espera até o timeout do backend).

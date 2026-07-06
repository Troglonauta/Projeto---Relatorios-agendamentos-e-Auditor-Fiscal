# Sprint 8 — Parte 2: Compliance & UX do Auditor Fiscal (2026-05-20)

Cinco frentes focadas no Auditor Fiscal — todos os ajustes vivem em
[`backend/fiscal/auditor.py`](../backend/fiscal/auditor.py),
[`backend/fiscal/comparators.py`](../backend/fiscal/comparators.py),
[`backend/routers/fiscal_routes.py`](../backend/routers/fiscal_routes.py),
[`backend/queue/tasks/fiscal_task.py`](../backend/queue/tasks/fiscal_task.py),
[`backend/timeutils.py`](../backend/timeutils.py) (novo) e
[`frontend/js/fiscal.js`](../frontend/js/fiscal.js).

| # | Frente | Status |
|---|---|---|
| 1 | Timezone BRT (Celery + audited_at) | ✅ |
| 2 | Filtros novos (hora, chave, modelos) | ✅ |
| 3 | Falsos positivos (severidade `pending`) | ✅ |
| 4 | Export XLSX multi-aba | ✅ |
| 5 | 3 novos comparators de compliance SPED | ✅ |

---

## 1️⃣ Timezone Brasília (BRT)

### Diagnóstico
Anomalias salvavam `datetime.utcnow()` (naive UTC) → UI exibia "17:21" quando
o operador esperava "14:21" (Brasília UTC-3).

### Fix
Novo módulo [`backend/timeutils.py`](../backend/timeutils.py):

```python
from zoneinfo import ZoneInfo
BRT_TZ = ZoneInfo("America/Sao_Paulo")

def now_brt() -> datetime:
    """Naive datetime em BRT (compatível com Column DateTime() existentes)."""
    return datetime.now(BRT_TZ).replace(tzinfo=None)
```

- `FiscalAnomaly.audited_at` agora usa `default=lambda: _brt_now_for_models()`
- `auditor.py` e `fiscal_routes.py` (ack/snooze/summary) substituem
  `datetime.utcnow()` por `now_brt()`
- Celery já estava em `timezone="America/Sao_Paulo"` + `enable_utc=False`

Fallback: se `tzdata` não estiver disponível em Windows, cai para offset estático
UTC-3 (Brasil aboliu DST em 2019 por decreto).

> ℹ️ **Decisão consciente**: armazenamos **naive-BRT** porque o resto das
> Columns do projeto são naive. Quando a app for migrada para
> `DateTime(timezone=True)` em UTC aware, trocamos `now_brt()` por
> `datetime.now(BRT_TZ)` sem mexer nos callers.

### Smoke test
```
BRT now: 2026-05-20 15:30:06 | UTC now: 2026-05-20 18:30:06 | diff = 3.0h ✓
```

---

## 2️⃣ Filtros refinados do Builder do Auditor

`FiscalAuditRequest` ([`fiscal_routes.py`](../backend/routers/fiscal_routes.py))
ganhou 4 campos opcionais:

| Campo | Tipo | Comportamento |
|---|---|---|
| `hora_from` | `str` (HH:MM ou HHMM) | Adiciona `AND F1_HORA >= :hf` ao SELECT SF1 |
| `hora_to` | `str` | Adiciona `AND F1_HORA <= :ht` |
| `chave_filter` | `str` (44 dígitos) | **Override total** do período: `WHERE F1_CHVNFE = :chv` |
| `doc_models` | `List[str]` | Filtra por modelo da chave (chars 21-22): `{"55","57","65","58"}` |

### Helpers novos em `auditor.py`

```python
def _normalize_hour(h) -> Optional[str]:
    """'08:30'/'0830'/'08:30:45' → '08:30'; '25:00' → None."""

def _doc_model_in_set(chave: str, models: set[str]) -> bool:
    """True se chave[20:22] está no set."""
```

### Degrade gracioso de `F1_HORA`
Se a coluna não existir nesta release Protheus (`pyodbc.ProgrammingError`),
o auditor reauto-executa a query **sem** filtro de hora e loga `info`:
```
F1_HORA nao disponivel — auditoria sem filtro de hora
```

### UI ([`fiscal.js`](../frontend/js/fiscal.js))
Modal "Nova auditoria" agora tem:
- Bloco amarelo destacado **🎯 Chave de Acesso Específica** (override do período)
- Campos `Desde/Hora Início/Até/Hora Fim` em linha
- Checkboxes **NF-e (55) / CT-e (57) / NFC-e (65) / MDF-e (58) / Todos**
- Validação JS: quando `chave_filter` preenchida, período e filiais ficam opcionais

---

## 3️⃣ Falsos positivos — severidade `pending`

### Antes
`XmlNotFound` (NFStock retorna 404) gerava `FiscalAnomaly` com `severity="warn"`
e `xml_value="(XML nao disponivel na fonte)"`. Aparecia no quadro de
divergências misturado com NCM/CFOP errados, contaminando KPIs.

### Agora
```python
except XmlNotFound:
    anomaly = FiscalAnomaly(
        doc_key=chave, branch=branch,
        field_compared="xml_nao_encontrado",
        protheus_value=doc.get("F1_DOC", ""),
        xml_value="(XML ainda nao disponivel na fonte)",
        severity="pending",   # Sprint 8 Part 2 — NAO é warn
        job_id=job_id,
    )
    stats["docs_pending"] += 1   # contador separado de `anomalies`
```

- Coluna `severity` aceita 4 valores: `info | warn | critical | pending`
- `stats["anomalies"]` conta APENAS `warn` + `critical` (divergências reais)
- `stats["docs_pending"]` conta separadamente as pendências

### UI dividida em tabs
[`fiscal.js`](../frontend/js/fiscal.js):

```
🚨 Anomalias [12]    ⏳ XMLs Pendentes na Fonte [47]
```

A aba "Pendentes" mostra disclaimer em amarelo explicando que **não são
divergências**, apenas notas que a fonte ainda não disponibilizou (SEFAZ
pode demorar até 48h, NFStock até 24h dependendo do convênio).

O dropdown de severidade ganhou opção `Pendentes na fonte` (para casos onde
o usuário quer filtrar a aba "Anomalias" focando só nas pendentes — embora
a UI já mostre as pendentes em aba separada por default).

---

## 4️⃣ Export XLSX multi-aba (Anomalias + Pendentes)

[`/api/fiscal/anomalies/export?fmt=xlsx`](../backend/routers/fiscal_routes.py)
gera workbook com 2 sheets:

### Aba 1 — "Anomalias Encontradas"
- Header **verde Fertimaxi** (`#2E8B3D`) com fonte branca bold, freeze pane A2
- Cols: ID, Auditado em (BRT), Chave NFe, Filial, CNPJ Fornecedor, Campo,
  Valor Protheus, Valor XML, Severidade, Reconhecido em, Snooze até, Observação
- Auto-filter habilitado (linha 1 vira combo do Excel)
- Larguras AutoFit baseadas em sample de 200 linhas
- Conteúdo: severity ∈ {`warn`, `critical`, `info`}

### Aba 2 — "XMLs Pendentes na Fonte"
- Header **laranja** (`#E67E22`) para distinguir visualmente (cor de "aviso")
- Cols: ID, Auditado em (BRT), Chave NFe, Filial, F1_DOC (Protheus), Status,
  Tentativa, Observação
- Conteúdo: severity = `pending`

### CSV legado
Mantido como opção secundária — uma única lista flat com coluna `aba`
(`"anomalia"` ou `"pendente"`) para integrações antigas.

### Smoke test
```
Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
Size: 8673 bytes
Abas: ['Anomalias Encontradas', 'XMLs Pendentes na Fonte']
  Anomalias Encontradas: 40r x 12c, freeze=A2, fill=FF2E8B3D, bold=True
  XMLs Pendentes na Fonte: 3r x 8c, freeze=A2, fill=FFE67E22, bold=True
```

---

## 5️⃣ Compliance estendido — 3 novos comparators

Decisão CTO: severidade **warn** (não-bloqueante) — visibilidade sem travar
operação. Apêndice em [`comparators.py`](../backend/fiscal/comparators.py).

### Regra 12 — UF do emitente (`compare_uf_emitente`)

**Por quê**: a chave NFe codifica a UF nos 2 primeiros dígitos (cUF IBGE).
Se a UF emitente diverge do cadastro `SA2.A2_EST`, é sinal de **operação
interestadual mal classificada** → risco de ICMS-ST não recolhido + auto
de infração SPED.

```python
chave[0:2] (cUF IBGE) → mapeia para sigla via _IBGE_UF_MAP
                                vs
SA2.A2_EST (cadastro do fornecedor)
```

Mapa IBGE completo (27 UFs + DF) embutido — sem dependência de tabela externa.

### Regra 13 — Inscrição Estadual do fornecedor (`compare_supplier_ie`)

**Por quê**: NFe com IE divergente da cadastrada gera rejeição no SINIEF
e atrai fiscalização sobre suspeita de operação com **IE suspensa**.

```python
SA2.A2_INSCR (Protheus)  vs  XML <emit><IE>
```

Trata `ISENTO`/`ISENTA` (literais SEFAZ) — não gera divergência quando
ambos os lados são isentos.

### Regra 14 — EAN/GTIN do produto (`compare_ean_gtin`)

**Por quê**: a SEFAZ valida o GTIN no XML contra o cadastro nacional.
Cadastro divergente gera **multa por declaração incorreta** no SPED. Comum
em itens importados onde o fornecedor usa código próprio.

```python
SB1.B1_CODBAR (cadastro de produto)  vs  XML <prod><cEAN>
```

`cEAN = "SEM GTIN"` (literal SEFAZ para produto sem código): skip silencioso.
Fallback para `<cEANTrib>` quando `<cEAN>` ausente.

### Hooked em `_compare_doc`

Comparators 12-14 são chamados ao final de `_compare_doc`:

```python
# 12) UF emitente — chave[0:2] vs SA2.A2_EST. Warn.
d = comparators.compare_uf_emitente(F1_CHVNFE, sa2.A2_EST)

# 13) IE fornecedor — XML <IE> vs SA2.A2_INSCR. Warn.
d = comparators.compare_supplier_ie(sa2.A2_INSCR, xml_header.ie_emitente)

# 14) EAN/GTIN por item — XML <cEAN> vs SB1.B1_CODBAR. Warn.
for pi in protheus_doc.items:
    d = comparators.compare_ean_gtin(sb1.B1_CODBAR, xi.ean)
```

### Schema atualizado

```python
SA2_COLS = "..., A2_CGC, A2_INSCR, A2_EST"   # Sprint 8 Part 2
SB1_COLS = "B1_COD, B1_DESC, B1_POSIPI, B1_CODBAR"
```

`_parse_nfe_header` agora extrai `ie_emitente` (`<emit><IE>`).
`_parse_nfe_items` agora extrai `ean` (`<prod><cEAN>` ou `<cEANTrib>`).

---

## ✅ Smoke tests Sprint 8 Part 2

```
Timezone:
  BRT now: 2026-05-20 15:30 | UTC now: 18:30 | diff 3.0h ✓

Filtros:
  _normalize_hour('08:30')   → '08:30' ✓
  _normalize_hour('0830')    → '08:30' ✓
  _normalize_hour('25:00')   → None     ✓
  _doc_model_in_set(NFe55_chave, {'55'}) → True
  _doc_model_in_set(NFe55_chave, {'57'}) → False

Pending:
  FiscalAnomaly(severity='pending') aceita ✓
  stats["docs_pending"] separado de stats["anomalies"] ✓

Comparators novos:
  compare_ean_gtin('7891...103', '7891...104') → Divergence warn ✓
  compare_ean_gtin('7891...103', 'SEM GTIN')   → None (skip)     ✓
  compare_supplier_ie('123', '456')            → Divergence warn ✓
  compare_supplier_ie('ISENTO', '999')         → None (skip)     ✓
  compare_uf_emitente(chave_SP, 'RJ')          → Divergence warn ✓
  compare_uf_emitente(chave_SP, 'SP')          → None            ✓

XLSX export:
  Abas: ['Anomalias Encontradas', 'XMLs Pendentes na Fonte']
  Anomalias Encontradas: 40r × 12c, freeze=A2, fill=FF2E8B3D ✓
  XMLs Pendentes na Fonte: 3r × 8c, freeze=A2, fill=FFE67E22 ✓

End-to-end (XML real):
  Header ie_emitente: 110042490114 ✓
  Items[0] ean: 7891000100103 ✓
  Divergencias: ['sdt_falta_item_1', 'ckocol_ausente',
                 'uf_emitente', 'ie_fornecedor', 'item_1_ean_gtin']

Security audit:
  CRITICAL=0  HIGH=0  MEDIUM=0  →  AMBIENTE EM CONFORMIDADE
```

---

## 🧪 Como validar em produção

### Timezone BRT
1. Rodar auditoria às 14:21 BRT → painel mostra "Auditado em: 14:21" (não 17:21).
2. Filtro `audited_at >= ontem` deve usar BRT também — `summary` e
   `list_anomalies` foram migrados.

### Filtros novos
1. **Chave específica**: cole 44 dígitos no campo amarelo → submit → auditor
   varre só essa chave (ignora período/filiais).
2. **Hora**: período 19/05 08:00 às 19/05 12:00 → apenas notas emitidas no
   intervalo da manhã.
3. **Modelos**: marque só "CT-e (57)" + Fonte = NFStock → varredura ignora
   NF-e e processa só CT-e.

### Pendentes
1. Rode auditoria contra fonte que ainda não tem alguns XMLs.
2. Aba "🚨 Anomalias" mostra somente as **divergências reais** — sem ruído
   das pendentes.
3. Aba "⏳ XMLs Pendentes na Fonte" lista as que ainda não chegaram com
   disclaimer explicativo.

### Export XLSX
1. Clique "📥 Exportar relatório" → "Excel (.xlsx)" → abra no Excel.
2. Confira 2 abas com headers verde (Anomalias) e laranja (Pendentes),
   freeze panes ativo, auto-filter na linha 1.

### Compliance estendido
1. Rode auditoria contra uma NF com:
   - Fornecedor cadastrado em RJ mas chave codificada SP → anomalia
     `uf_emitente`.
   - SA2.A2_INSCR diferente do `<IE>` do XML → anomalia `ie_fornecedor`.
   - SB1.B1_CODBAR diferente do `<cEAN>` por item → anomalia
     `item_N_ean_gtin`.
2. Todas com severidade **warn** (não-bloqueante).

---

## ⏭️ Próximas sugestões

- **CT-e/MDF-e real**: `_load_period_with_extras` ainda só pesca SF1
  (entrada de NF-e). Para auditar CT-e de fato, expandir com SFK/SFA.
- **Regra 15 (CEST)**: NFe traz `<CEST>` — compara com SB1.B1_CEST quando
  cadastrado. Ainda mais comum gerar autuação que NCM.
- **Regra 16 (CST PIS/COFINS)**: hoje só checamos ICMS no SF3. Adicionar
  comparação dos códigos CST PIS/COFINS do XML vs SFT.
- **Coluna `audited_at` com timezone**: migrar `Column(DateTime(timezone=True))`
  e armazenar UTC aware. UI converte ao renderizar. Ganho real só quando a
  Fertimaxi expandir para outras filiais fora de Brasília.

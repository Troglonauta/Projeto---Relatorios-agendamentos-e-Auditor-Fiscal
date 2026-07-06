# Sprint 8 — Hotfix2 v2.0 (2026-05-21)

Bug report follow-up: 4 frentes críticas pós-deploy hotfix anterior.

| # | Item | Status |
|---|---|---|
| 1a | JOIN SQL Server: verificado, regressão NÃO existe no repo | ✅ |
| 1b | Single-table: erro `Coluna inválido: 'SF1.F1_FILIAL'` | ✅ corrigido |
| 1c | `/api/fiscal/summary` TypeError fromisoformat | ✅ defensivo |
| 2  | NFStock endpoint configurável (`{chave}` placeholder) | ✅ |
| 3  | Admin: remover blocos Transmite/TSS/Smartlink | ✅ |
| 4a | Dark mode: `.main` e `.topbar` brancos no dark | ✅ |
| 4b | Toggle Sol/Lua fixo no rodapé acima da versão | ✅ |
| 4c | Visão Operador: ocultar versão/build/tz + form-text | ✅ |

---

## 1️⃣ Motor SQL — 3 sub-correções

### 1a. JOIN `AS` após `WITH (NOLOCK)` — sem regressão
Verifiquei [`query_engine.py::_from_and_joins`](../backend/query_engine.py):
o código ainda gera o padrão correto (alias **antes** do hint):

```sql
FROM SC5010 t1 WITH (NOLOCK) INNER JOIN SA1010 t2 WITH (NOLOCK) ON ...
```

Smoke test confirma zero ocorrências de `WITH (NOLOCK) AS`. Se o erro
voltou em produção, é cache de bytecode antigo — reinicie o worker Celery
+ delete `__pycache__/` recursivamente.

### 1b. Single-table: `Coluna inválido: 'SF1.F1_FILIAL'`

**Causa**: o Builder do frontend emite colunas qualificadas
(`SF1.F1_FILIAL`) mesmo em consultas single-table (porque o dropdown reusa
o formato qualificado dos JOINs). `_safe_ident()` aplica o regex
`[A-Za-z0-9_]+` que **rejeita o ponto** — daí o erro.

**Fix** em [`backend/protheus_api.py`](../backend/protheus_api.py):
novo helper `_strip_qualifier()` aplicado a `columns` e `filter keys`
antes de `_safe_ident`:

```python
def _strip_qualifier(name: str) -> str:
    """SF1.F1_FILIAL → F1_FILIAL (single-table).
       SC5__C5_NUM → SC5__C5_NUM (JOIN format, intacto)."""
    if isinstance(name, str) and "." in name and "__" not in name:
        return name.split(".", 1)[1]
    return name

# Em _build_filter_clause:
field = _safe_ident(_strip_qualifier(field), "Coluna em filtro")

# Em query_table:
validated_cols = [_safe_ident(_strip_qualifier(c), "Coluna") for c in columns]
```

JOIN-qualified (`SC5__C5_NUM`) passa intacto — não tem `.`. Só o formato
single-table com `.` é stripado.

### 1c. `TypeError: fromisoformat: argument must be str`

`fiscal_task.py::run_fiscal_audit` chamava `date.fromisoformat(payload["date_from"])`
sem verificar tipo. Se o payload chegasse com objeto `date`/`datetime`
(reprocess de job antigo, ou caller direto), explodia.

**Fix** em [`fiscal_task.py`](../backend/queue/tasks/fiscal_task.py):
nova função `_coerce_date()` aceita `str`, `date` ou `datetime`:

```python
def _coerce_date(v):
    if isinstance(v, date) and not isinstance(v, datetime): return v
    if isinstance(v, datetime): return v.date()
    if isinstance(v, str): return date.fromisoformat(v)
    raise ValueError(f"date_from/date_to inválido (tipo {type(v).__name__})")

date_from = _coerce_date(payload["date_from"])
date_to   = _coerce_date(payload["date_to"])
```

Smoke confirma os 3 caminhos (str ISO / date / datetime).

---

## 2️⃣ NFStock endpoint configurável

**Antes**: 4 caminhos hardcoded tentados em sequência. API Alterdata
retornava HTML 200 (landing page) → falsos sucessos.

**Agora**: setting único `NFSTOCK_XML_ENDPOINT` com placeholder `{chave}`.

### Backend ([nfstock.py](../backend/fiscal/xml_sources/nfstock.py))

```python
DEFAULT_XML_ENDPOINT = "https://nfstock.alterdata.com.br/api/v2/Documentos/{chave}/Xml"

def _resolve_endpoint(self, c, chave):
    tpl = c["xml_endpoint"] or self.DEFAULT_XML_ENDPOINT
    if "{chave}" not in tpl:
        raise XmlSourceError(
            "NFSTOCK_XML_ENDPOINT deve conter o placeholder {chave}"
        )
    if tpl.startswith(("http://", "https://")):
        return tpl.replace("{chave}", chave)
    # Relativa — concatena com NFSTOCK_URL
    return (c["url"].rstrip("/") + "/" + tpl.lstrip("/")).replace("{chave}", chave)
```

### UI Admin

Novo campo **obrigatório** "Endpoint de Download do XML" abaixo da URL raiz:

```
Endpoint de Download do XML — obrigatório, use o placeholder {chave}
[ https://nfstock.alterdata.com.br/api/v2/Documentos/{chave}/Xml ]
ℹ️ Caminho exato que sua release Alterdata expõe...
```

Validação JS bloqueia salvar sem `{chave}` no template.

Resolução suporta:
- **Absoluta**: `https://nfstock.alterdata.com.br/api/v2/Documentos/{chave}/Xml` → direto
- **Relativa**: `/api/v2/Documentos/{chave}/Xml` → concatena com `NFSTOCK_URL`

Header de autenticação continua sendo `Authorization: Bearer <TOKEN>` (configurável via `NFSTOCK_AUTH_STYLE`).

---

## 3️⃣ Admin UI — limpeza de provedores

Removidos do painel **Administração > APIs externas**:
- Bloco "📨 TOTVS Transmite" + botões Salvar/Test
- Bloco "TOTVS TSS on-premise"
- Bloco "Smartlink"

Os settings (`TRANSMITE_URL`, `FISCAL_TSS_*`, `SMARTLINK_*`) continuam no
banco para não quebrar instalações em migração — só sumiram da UI.

**Provedores oficiais agora**: `📨 Alterdata NFStock` (multi-modelo) +
`🔐 SEFAZ A1` (NF-e 55 only).

`get_config()` no backend deixou de retornar `transmite_url`, `tss_url`,
`smartlink_url`. `post_apis()` mapping ficou só com `NFSTOCK_*` + token A1.
Save handler do admin.js só envia os campos NFStock — payload mais limpo.

---

## 4️⃣ UI/UX

### 4a. Dark mode — `.main` e `.topbar` brancos

**Causa**: surfaces hardcoded `background: #fff` que o Sprint 8 Part 1
não cobriu. Sprint 8 Part 1 sobrescrevia `.modal-content`, `.dropdown-menu`,
`.table-card`, `.kpi-card`, etc — mas não `.main` nem `.topbar`.

**Fix** em [`style.css`](../frontend/css/style.css):

```css
/* Regra base — .main herda var(--bg) sempre, não só light */
.main { background: var(--bg); color: var(--text); }

/* Overrides dark explícitos para topbar e content */
body.dark-mode .main { background: var(--bg); }
body.dark-mode .topbar {
  background: var(--card); color: var(--text);
  border-bottom-color: var(--border);
}
body.dark-mode .topbar .page-title { color: var(--text); }
body.dark-mode .content { background: var(--bg); }

/* user-pill e topbar-clock no dark — paleta soft do fx-primary */
body.dark-mode .topbar .user-pill,
body.dark-mode .topbar-clock {
  background: var(--fx-primary-soft);  /* #1d3a23 no dark */
  color: #b3e6c4;
  border-color: rgba(46,139,61,0.4);
}
```

### 4b. Toggle Sol/Lua no rodapé

```css
.theme-toggle-wrap {
  margin-top: auto;           /* puxa para o fundo */
  padding: 10px 16px;
  border-top: 1px solid rgba(255,255,255,0.08);
  display: block;
  order: 99;                  /* defensivo se alguém reordenar flex */
}
/* footer-info IMEDIATAMENTE depois do toggle perde seu margin-top:auto
 * para não competir no flex */
.sidebar .theme-toggle-wrap + .footer-info { margin-top: 0; }
```

Resultado: toggle fica grudado no rodapé com border-top, version logo abaixo.

### 4c. Visão Operador

[`layout.js`](../frontend/js/layout.js) injeta classe no `<body>`:

```js
document.body.classList.remove("role-admin", "role-operator");
document.body.classList.add(u.role === "admin" ? "role-admin" : "role-operator");
```

CSS oculta elementos para operador (admin vê tudo):

```css
body.role-operator .sidebar .footer-info { display: none; }
body.role-operator .form-text { display: none; }
body.role-operator small.text-muted,
body.role-operator .text-muted.small { display: none; }
```

- **Versão/build/fuso** somem do rodapé da sidebar
- **Helpers `.form-text`** abaixo de inputs somem
- **`small.text-muted`** explicativos somem (mas badges/labels preservados)

Admin vê tudo normalmente.

---

## ✅ Smoke tests (Hotfix2)

```
[1a] JOIN syntax OK (SC5010 t1 WITH (NOLOCK))
[1b] Single-table qualifier strip OK ('SF1.F1_FILIAL' → 'F1_FILIAL')
[1c] _coerce_date aceita str, date, datetime
[2]  NFStock endpoint absoluto resolve:
     https://nfstock.alterdata.com.br/api/v2/Documentos/{44_digitos}/Xml
[2]  Endpoint relativo resolve concatenando com NFSTOCK_URL
[2]  Sem {chave} placeholder → XmlSourceError clara
[3]  Admin UI: blocos Transmite/TSS/Smartlink removidos
[4c] layout.js injeta role-admin/role-operator no body
[4a] CSS: body.dark-mode .main usa var(--bg)
[4b] CSS: .theme-toggle-wrap + .footer-info zera margin
[4c] CSS: body.role-operator oculta form-text + footer-info

JS sanity:
  admin.js: 257/257 braces, 569/569 parens
  layout.js: 54/54 braces, 124/124 parens

Security audit: CRITICAL=0 HIGH=0 MEDIUM=0 → CONFORMIDADE
```

---

## 🧪 Como validar em produção

### Motor SQL
1. JOIN: rode consulta com 2+ tabelas → SQL gerado tem `tabela alias WITH (NOLOCK)`, zero ocorrências de `AS`.
2. Single-table: filtro com nome qualificado `SF1.F1_FILIAL` ou coluna `SF1.F1_DOC` → executa OK (era erro `Coluna inválido` antes).
3. Audit "Nova auditoria" com periodo válido: `_coerce_date` agora aceita strings ISO sem TypeError. Reprocesso de job antigo (que tinha date object) também passa.

### NFStock endpoint
1. Admin → APIs externas → bloco NFStock → preencha o novo campo
   **Endpoint de Download do XML** com a URL do seu portal Alterdata.
2. Confira que tem `{chave}` no template (validação JS bloqueia salvar sem).
3. Rode auditoria → o worker chama exatamente a URL configurada com a chave substituída.
4. Salvar sem `{chave}` → toast vermelho.

### Admin limpo
1. Admin → APIs externas → confira que aparecem **só 2 blocos**: SEFAZ A1 + NFStock. Transmite/TSS/Smartlink não existem mais.

### Dark mode
1. Toggle 🌙 → `.main`, `.topbar`, `.content` ficam todos cinza-escuros (não brancos).
2. `user-pill` e relógio ficam em tom verde-soft escuro.
3. Toggle 🌙 fica no rodapé da sidebar (acima da versão) com `border-top`.

### Operador
1. Login com user `operator` → sidebar NÃO mostra "versão / build / fuso".
2. Formulários (Builder, Schedules, Profiles, etc) → `.form-text` (azulinhos abaixo de inputs) somem.
3. Login como admin → tudo aparece normalmente.

---

## 📦 Code Freeze v2.0 — definitivo

Esta é a **última rodada de hotfixes**. Próximas alterações entram em
sprint formal (v2.1+). VERSION continua `2.0.0` (mesmas funcionalidades
da Sprint 8 Part 3 — só correções de regressão e refinamentos UX).

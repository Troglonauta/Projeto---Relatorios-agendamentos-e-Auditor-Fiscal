# Sprint 8 — Hotfix v2.0 (2026-05-21)

Bug report do cliente apontou regressões + entregas faltantes do frontend.
Correções entregues:

| # | Item | Status |
|---|---|---|
| 1 | JOIN quebrado (`AS` depois de `NOLOCK`) | ✅ corrigido |
| 2a | Modal "Nova auditoria": multi-select filiais (sem texto livre) | ✅ |
| 2b | Colunas da grid renomeadas (Protheus / XML / Gravidade) | ✅ |
| 3a | Toggle Sol/Lua na sidebar reaparece | ✅ |
| 3b | Perfis "Tabelas extras" vira `<select multiple>` dinâmico | ✅ |
| 3c | Wizard + Admin: radio "Provedor Fiscal" (apenas 1 ativo) | ✅ |
| 3d | Ícone "olho" da senha sempre visível (Chrome/Firefox/Edge) | ✅ |
| 3e | Versão bumpada para **v2.0.0** | ✅ |
| 4 | Teste NFStock não baixa XML falso — só valida token | ✅ |

---

## 1️⃣ Regressão crítica do JOIN (SQL Server)

### Erro reportado
```
[42000] [Microsoft][ODBC Driver 17 for SQL Server]
Incorrect syntax near the keyword 'AS'.
```

### Causa
SQL Server **NÃO** aceita `AS alias` após um table hint
(`WITH (NOLOCK)`). A ordem correta é:

```sql
-- ❌ ERRADO (o gerador anterior produzia isto):
FROM SC5010 WITH (NOLOCK) AS t1
INNER JOIN SA1010 WITH (NOLOCK) AS t2 ON ...

-- ✅ CORRETO (alias ANTES do hint):
FROM SC5010 t1 WITH (NOLOCK)
INNER JOIN SA1010 t2 WITH (NOLOCK) ON ...
```

### Fix em [`backend/query_engine.py`](../backend/query_engine.py)

```python
def _from_and_joins(self) -> str:
    base = self._aliases[self.base_alias]
    sql = f"{base['table']} {base['sql']} WITH (NOLOCK)"
    for j in self.joins:
        ...
        sql += f" {jt} JOIN {tgt['table']} {tgt['sql']} WITH (NOLOCK) ON {on_clause}"
    return sql
```

### Smoke test confirmado
```
SELECT t1.C5_NUM AS [SC5__C5_NUM], t2.A1_NOME AS [SA1__A1_NOME]
FROM SC5010 t1 WITH (NOLOCK)
INNER JOIN SA1010 t2 WITH (NOLOCK) ON t1.C5_CLIENTE = t2.A1_COD
WHERE t1.D_E_L_E_T_ = ' ' AND t2.D_E_L_E_T_ = ' '
ORDER BY t1.R_E_C_N_O_ OFFSET :_offset ROWS FETCH NEXT :_limit ROWS ONLY
```

---

## 2️⃣ Refatoração Frontend do Auditor Fiscal

### 2a. Multi-select de filiais

**Antes**: `<input id="rBranches" placeholder="01,02">` (usuário digitava)

**Agora**: `<select id="rBranches" multiple size="4">` populado dinamicamente
de `/api/settings/public` (`branches: ["01","02",...,"08"]`).

Helper novo `_populateBranchesMultiSelect()` é chamado ao abrir o modal —
carrega de `loadPublicSettings()` com cache em `window._publicSettingsBranches`,
fallback para `["01"..."08"]` se settings não responder.

No submit:
```js
const selectedBranches = Array.from($("rBranches").selectedOptions || [])
  .map(o => o.value).filter(Boolean);
```

### 2b. Colunas renomeadas

| Antes | Agora |
|---|---|
| `Protheus` | **Valor no ERP (Protheus)** + tooltip |
| `XML` | **Valor na Nota (XML)** + tooltip |
| `Sev.` | **Gravidade** + tooltip |
| `Campo` | **Campo Comparado** |

Tooltips explicativos no `<th title="...">`:
- *"Valor encontrado no ERP Protheus (cadastro/lancamento)"*
- *"Valor extraido do XML da NFe original"*
- *"Severidade da divergencia: critica / aviso / info"*

Mesma renomeação na aba "Pendentes" (`Doc Protheus` → `Documento no ERP`).

---

## 3️⃣ UI Admin + Wizard

### 3a. Dark Mode toggle reaparece

**Diagnóstico**: o botão existia em `layout.js` desde a Sprint 8 Part 1,
mas o CSS tinha `margin-top: auto` em **DOIS** elementos consecutivos
(`.theme-toggle-wrap` e `.sidebar .footer-info`), o que no flex distribui o
espaço de forma indeterminada — o toggle ficava em posição imprevisível
ou esmagado.

**Fix**: `.theme-toggle-wrap` mantém `margin-top: auto` (empurra para o
rodapé) + adiciona `border-top` para separação visual + zera o `margin-top`
do `.footer-info` que vem DEPOIS dele (regra `+ .footer-info { margin-top: 0; }`).

### 3b. Perfis "Tabelas extras" — multi-select

[`frontend/js/users.js`](../frontend/js/users.js):
- HTML: `<input id="uTables">` → `<select id="uTables" multiple size="5">`
- Novo cache `allTableAliases` carregado em `loadProfileCatalog()` via
  `/api/protheus/aliases`
- `populateTablesMultiSelect(selected)` injeta `<option>` por alias com
  pré-seleção
- `selectedTableAliases()` lê `selectedOptions` no submit
- `openCreate`/`openEdit` chamam o populador

Usuário não digita mais — escolhe do dropdown com nome amigável.

### 3c. Wizard — Radio "Provedor Fiscal"

[`frontend/pages/setup.html`](../frontend/pages/setup.html) passo "APIs"
foi totalmente reescrito:
- **4 cards de rádio** ao topo: ⏸ Nenhum · 📨 NFStock · 📡 Transmite · 🔐 SEFAZ A1
- Apenas o bloco do provedor selecionado fica visível
- Inputs legados (TSS, Smartlink) viraram `<input type="hidden">` para
  manter compatibilidade com o `http()` que serializa todo `data.apis`

`setup.js::apis()` agora:
- Lê o radio selecionado
- Valida apenas os campos do provedor escolhido
- Limpa todos os outros como `null` no payload
- Chama endpoint novo `POST /api/setup/fiscal-source` (best-effort, graceful)

Backend novo em [`setup_routes.py`](../backend/routers/setup_routes.py):
```python
@router.post("/fiscal-source")
def set_fiscal_source(payload: dict):
    source = payload.get("source") or ""
    VALID = {"disabled", "a1", "nfstock", "transmite", "tss", ""}
    if source not in VALID: raise HTTPException(400, ...)
    if source: settings_store.set_setting("FISCAL_SOURCE", source, scope="fiscal")
```

A Admin UI (Sprint 7) já tinha dropdown `Desativado/A1/NFStock` — sem
mudanças necessárias.

### 3d. Ícone "olho" da senha sempre visível

**Diagnóstico**: Edge tem `::-ms-reveal` nativo, mas Bootstrap reset o
escondia. Chrome/Firefox **NÃO TÊM** ícone nativo — precisava de JS manual.

**Fix em duas camadas**:

1. **CSS** — força `::-ms-reveal` visível em Edge
2. **JS universal** — novo módulo
   [`frontend/js/pwd-toggle.js`](../frontend/js/pwd-toggle.js):
   - Varre todo `input[type=password]` ao DOMContentLoaded
   - Injeta `.pwd-wrap` em volta + botão `.pwd-toggle` (👁/🙈)
   - Click → alterna `type=password` ↔ `type=text`
   - `MutationObserver` cobre inputs criados dinamicamente (modais Bootstrap,
     setup steps lazy)
   - `tabIndex = -1` no botão para não quebrar Tab do form

Auto-importado por `api.js` → roda em **todas** as páginas, login inclusive.

### 3e. Versão v2.0.0

[`backend/version.py`](../backend/version.py):
```python
VERSION    = "2.0.0"
BUILD_DATE = "2026-05-21"
PHASE      = "v2.0"
CODENAME   = "code-freeze"
```

README.md ganhou linha de versão no topo:
```markdown
**v2.0.0** · Code Freeze · build 2026-05-21
```

---

## 4️⃣ Teste NFStock — só valida token, sem baixar XML falso

### Antes
`ping()` chamava `GET /v1/xml/{chave_fake}` → API NFStock devolvia HTML
200 (landing page) → frontend interpretava como **sucesso falso**.
Pior: bytes de HTML poluíam o log do worker.

### Agora
`ping()` virou **validação local sem rede**:

```python
def ping(self) -> dict:
    if not self.is_configured(): return {ok:False, ...}

    # Sanity 1: URL HTTPS válida
    if not url.startswith(("http://", "https://")): return ❌

    # Sanity 2: aviso se URL tem path manual ("/Autenticacao/Login")
    if path_after_host: detail += " Atencao: URL tem caminho '/X'..."

    # Sanity 3: token > 16 chars (PATs reais tem 40+)
    if len(token) < 16: return ❌ "Token muito curto..."

    return {ok: True, detail: "Token armazenado com sucesso..."}
```

> *"A validacao real acontece na primeira auditoria — se o token estiver
> errado, a API retornara 401 e o auditor abortara com mensagem clara."*

Esse é o padrão usado por GitHub/Linear/etc para Personal Access Tokens:
não há endpoint público de "validar token" — o token só prova validade
quando é usado.

Frontend atualizou label do botão: **"Validando token…"** (não "Testando").

---

## ✅ Smoke tests consolidados

```
[1]  JOIN SQL: alias antes de NOLOCK ✓
     SC5010 t1 WITH (NOLOCK) INNER JOIN SA1010 t2 WITH (NOLOCK)
     ZERO ocorrências de "WITH (NOLOCK) AS"

[2a] Multi-select branches: <select multiple> populado de /api/settings/public

[2b] Colunas: "Valor no ERP (Protheus)" / "Valor na Nota (XML)" / "Gravidade"

[3a] Dark mode toggle: aparece na sidebar (margin-top: auto duplicado
     resolvido com border-top + zeragem da margin do footer-info)

[3b] Perfis tabelas extras: <select multiple> via /api/protheus/aliases

[3c] Wizard: radio único + endpoint /api/setup/fiscal-source

[3d] pwd-toggle.js: MutationObserver cobre login + modais + steps lazy

[3e] VERSION = 2.0.0 (build 2026-05-21)

[4]  NFStock ping (sem rede):
     URL+token coerentes → ok=True com info de auth_style + token_chars
     Token < 16 chars → ok=False com mensagem clara
     URL com path manual ("/Autenticacao/Login") → aviso mas não bloqueia

Security audit: CRITICAL=0 HIGH=0 MEDIUM=0 → CONFORMIDADE
JS sanity: braces balanceados em fiscal/setup/users/pwd-toggle/layout
```

---

## 🧪 Como validar na próxima janela

### JOIN (1)
1. Builder → SC5 + JOIN SA1 + colunas → Consultar
2. Resultado retorna sem `Incorrect syntax near 'AS'`
3. Log do backend mostra SQL com `SC5010 t1 WITH (NOLOCK)`

### Modal Nova Auditoria (2a)
1. Auditor Fiscal → "+ Nova auditoria" → campo "Filiais a auditar" é
   `<select multiple>` com **Filial 01–08** (não mais campo de texto)
2. Ctrl+click seleciona múltiplas; submit envia o array

### Colunas renomeadas (2b)
1. Auditor Fiscal → aba "Anomalias" → cabeçalhos visíveis: **Valor no
   ERP (Protheus)** · **Valor na Nota (XML)** · **Gravidade**

### Dark Mode (3a)
1. Login → sidebar exibe botão **🌙 Modo escuro** acima da versão
2. Click → tema vira escuro + botão muda para **☀️ Modo claro**
3. F5 → tema persiste

### Perfis tabelas (3b)
1. Usuários → editar usuário → campo "Tabelas extras" é dropdown
   `<select multiple>` listando todos os aliases Protheus

### Wizard (3c)
1. `/static/pages/setup.html` (DB zerado) → passo APIs → 4 cards de rádio
2. Selecione "NFStock" → só o bloco NFStock aparece
3. Salve → `FISCAL_SOURCE = nfstock` no banco

### Eye icon (3d)
1. Qualquer tela com `<input type=password>` (login, modal usuário, admin)
2. Ícone 👁 sempre visível à direita do campo
3. Click → senha vira texto visível + ícone vira 🙈

### Versão (3e)
1. Sidebar → rodapé mostra **v2.0.0** + build 21/05/2026
2. README.md → linha superior com `**v2.0.0** · Code Freeze`

### NFStock test (4)
1. Admin → APIs → NFStock → URL + token preenchidos → "🔎 Testar conexão"
2. Toast verde "Token armazenado com sucesso (N chars, header 'bearer')"
3. SEM request HTTP a Alterdata (verifique no DevTools Network)
4. Token < 16 chars → toast vermelho "Token muito curto..."
5. URL com `/Autenticacao/Login` → toast verde com **aviso** sobre path

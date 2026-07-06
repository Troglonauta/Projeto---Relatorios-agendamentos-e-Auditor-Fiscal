# Sprint 8 — Parte 1: UX Enterprise no Builder (2026-05-20)

Três frentes de "ferramentas de luxo" para a Controladoria:

| # | Frente | Status | Onde |
|---|---|---|---|
| 1 | **Consultas Salvas server-side** (substitui localStorage) | ✅ | Backend novo + `protheus.js` |
| 2 | **Preview Inline** (Mini-Excel TOP 100) | ✅ (já existia) | `protheus.js` |
| 3 | **Dark Mode** + Skeleton extras | ✅ | `theme.js` + `style.css` |

---

## 1️⃣ Consultas Salvas — Migração localStorage → SQLite

### Antes (Sprint 8 original)
Modelos viviam em `localStorage.pr_saved_models_v1` — per-browser, per-máquina.
Operador perdia tudo ao trocar de PC e não dava para compartilhar entre time.

### Agora (Sprint 8 Part 1)

**Backend novo** — model `SavedQuery` em
[`backend/models.py`](../backend/models.py):

```python
class SavedQuery(Base):
    __tablename__ = "saved_queries"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_user_query_name"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    payload_json = Column(Text, nullable=False)
    created_at, updated_at = ...
```

Router novo em
[`backend/routers/saved_queries_routes.py`](../backend/routers/saved_queries_routes.py):

| Verbo | Path | Função |
|---|---|---|
| GET    | `/api/saved-queries` | Lista os meus |
| POST   | `/api/saved-queries` | **Upsert por (user_id, name)** |
| PUT    | `/api/saved-queries/{id}` | Renomeia ou atualiza |
| DELETE | `/api/saved-queries/{id}` | Apaga |

**Guard rails**:
- Payload máximo 32 KB (impede spam)
- Máx 100 modelos por usuário
- `UniqueConstraint(user_id, name)` evita duplicatas; cada usuário tem seu próprio namespace
- `ForeignKey("users.id", ondelete="CASCADE")` — apaga junto quando user é hard-deleted

**Frontend** — `protheus.js` foi migrado:
- Cache em memória `_savedModels = []` ressincronizado em cada operação
- `_fetchSavedModels()` recarrega do servidor
- Botão "💾 Salvar Modelo" agora é **verde sólido** (Bootstrap `btn-success`),
  com spinner integrado durante o POST
- **Migração automática one-shot**: na primeira execução pós-upgrade, qualquer
  modelo em `localStorage.pr_saved_models_v1` é enviado via POST e a chave
  local é removida (com marca `_migrated_at` no localStorage para idempotência)

```js
async function _migrateLocalStorageOnce() {
  const raw = JSON.parse(localStorage.getItem("pr_saved_models_v1") || "[]");
  if (!raw.length) return;
  for (const m of raw) {
    if (existingNames.has(m.name)) continue;
    await api("/api/saved-queries", { method: "POST",
      body: { name: m.name, payload: { ... } }});
  }
  localStorage.setItem("pr_saved_models_v1_migrated_at", new Date().toISOString());
  localStorage.removeItem("pr_saved_models_v1");
  toast(`${migrated} modelo(s) migrado(s) do navegador para o servidor`, "success");
}
```

### Smoke test
```
INSERT OK: id=1
UNIQUE (user_id, name) OK
2 users com mesmo nome OK (id=42)   ← namespace por usuário funciona
Cleanup OK
```

---

## 2️⃣ Visualizar Amostra — refinamento

Já estava implementado na Sprint 8 (botão `👁️ Visualizar Amostra` ao lado de
"Consultar"). Pequenos ajustes:
- Cor agora **azul sólido** (`btn-info text-white`) — antes era outline
- Skeleton ativa antes do request (consistência com `Consultar`)
- Subtítulo do grid muda para `👁️ Amostra · {table} · primeiras N linhas`

Comportamento: força `payload.page = 1, page_size = 100`, mantém o resto do
payload do Builder. Sem job de fila, sem download — pura validação visual.

---

## 3️⃣ Dark Mode + Skeleton extras

### Estratégia
- **CSS variables** já existiam (`--bg`, `--card`, `--text`, `--muted`,
  `--border`). Só adicionamos um bloco `body.dark-mode {...}` que sobrescreve.
- **Bootstrap 5.3 native dark**: aplicamos `data-bs-theme="dark"` no `<html>`
  — ativa estilos dark de dropdown/modal/toast sem reescrever.
- **Verde Fertimaxi preservado**: `--fx-primary` continua `#2E8B3D`, só os
  tons "soft" ganham variante mais escura para contraste no fundo cinza.

### Novos arquivos / mudanças

**`frontend/js/theme.js` (novo)** — módulo standalone:
```js
export function getTheme()    { return localStorage.pr_theme === "dark" ? "dark" : "light"; }
export function setTheme(t)   { /* aplica + persiste + dispara theme:changed */ }
export function toggleTheme() { setTheme(getTheme() === "dark" ? "light" : "dark"); }
export function applyThemeFromStorage() { /* roda no boot */ }

// Auto-bootstrap ao importar
applyThemeFromStorage();
```

**`frontend/js/api.js`** — importa `theme.js` no topo (executa ANTES do
render → sem flash branco em refresh):
```js
import { applyThemeFromStorage } from "./theme.js";
applyThemeFromStorage();
```

**`frontend/js/layout.js`** — botão Sol/Lua no sidebar (entre nav e versão):
```html
<button id="btnThemeToggle" class="theme-toggle-btn">
  <span id="themeIcon">🌙</span>
  <span id="themeLabel">Modo escuro</span>
</button>
```

Click → `toggleTheme()` → atualiza ícone/label imediatamente.
Página recarrega? Tema persiste via `localStorage.pr_theme`.

### Paleta dark Fertimaxi

| Variable | Light | Dark |
|---|---|---|
| `--bg` | `#f4f7f5` | `#1a1d1f` |
| `--card` | `#ffffff` | `#25292c` |
| `--text` | `#1f2d3d` | `#e6e8ea` |
| `--muted` | `#6c757d` | `#8a9099` |
| `--border` | `#dde3e8` | `#3a3f44` |
| `--fx-primary` | `#2E8B3D` | `#2E8B3D` (preservado) |
| `--fx-primary-soft` | `#e7f3e9` | `#1d3a23` (escurecido) |

Ajustes pontuais para componentes que tinham cor hardcoded:
- `.login-shell` ganha gradient cinza-esverdeado no dark
- `.table` força `--bs-table-bg: transparent` para herdar `--card`
- `.form-control`/`.form-select` com `#1f2326` (mais escuro que card) para
  hierarquia visual
- `code`/`pre` ganham background `#2a2f33` e texto claro
- `.alert-info` muda para `#1d2e3a` + texto azul claro

### Skeleton loaders estendidos

Sprint 8 já tinha `.skeleton-bar`. Adicionei 3 variantes:

```css
.skeleton-text   /* linha de 14px de altura — títulos/labels */
.skeleton-circle /* 32×32 — avatares/bullets de KPI */
.skeleton-card   /* container 90px+ com border + padding */
```

Todas com `@keyframes skeleton-shimmer` e variante dark (`rgba(255,255,255,*)`
em vez de `rgba(0,0,0,*)`) automática via `body.dark-mode`.

---

## ✅ Smoke tests (Sprint 8 Part 1)

```
Backend:
  SavedQuery table: saved_queries
  cols: id, user_id, name, payload_json, created_at, updated_at
  endpoints registrados: 4 (GET/POST/PUT/DELETE)
  Total rotas no app: 102

CRUD:
  INSERT OK
  UNIQUE (user_id, name) constraint ✓
  Dois usuários com mesmo nome ✓ (namespace por user funciona)
  Cleanup ✓

Frontend:
  protheus.js: braces 340/340, parens 664/664 — balanceado
  Zero referências legacy a localStorage saved_models
  theme.js exports getTheme/setTheme/toggleTheme/applyThemeFromStorage

CSS:
  Dark mode override block ativo em body.dark-mode
  data-bs-theme="dark" injetado em <html> via theme.js
  Skeleton extras: .skeleton-text, .skeleton-circle, .skeleton-card

Security audit:
  CRITICAL=0  HIGH=0  MEDIUM=0  → AMBIENTE EM CONFORMIDADE
```

---

## 🧪 Como validar em produção

### Consultas Salvas server-side
1. Builder → SC5 + filial 01 + filtro qualquer → **💾 Salvar Modelo** →
   nome "Pedidos jan/26" → toast "salvo no servidor".
2. Abra outro navegador / outra máquina → login com o mesmo usuário → o
   modelo aparece no dropdown "Meus Modelos".
3. Login com outro usuário → o modelo do colega NÃO aparece (namespace
   por `user_id` funciona).
4. Sessão antiga com modelos em localStorage? Na primeira carga, vê toast
   "N modelo(s) migrado(s) do navegador para o servidor" e o localStorage
   é limpo.

### Visualizar Amostra
1. Builder → escolha colunas → **👁️ Visualizar Amostra** (botão azul) →
   tabela aparece logo abaixo com até 100 linhas; sem download de XLSX.

### Dark Mode
1. Sidebar → botão **🌙 Modo escuro** → tela fica cinza-escura, verde
   Fertimaxi preservado nos CTAs e KPIs.
2. Botão muda para **☀️ Modo claro**.
3. Refresh F5 → tema permanece (persistido em `localStorage.pr_theme`).
4. Logout / login em outro navegador → tema é per-browser por design.

---

## ⏭️ Próximas sugestões

- **Compartilhar modelos**: hoje cada usuário tem o seu. Adicionar coluna
  `is_shared` para o admin marcar modelos disponíveis ao time.
- **Importar/Exportar JSON**: botão para baixar `.json` do modelo e
  colar em outra instância (dev → prod).
- **Tema baseado em horário**: detectar `prefers-color-scheme: dark` do SO
  como default quando o usuário nunca trocou manualmente.
- **Skeleton em mais telas**: aplicar `.skeleton-card` nos KPIs do Dashboard
  durante o `await Promise.all([loadKpis, loadFiscal, loadFeed])`.

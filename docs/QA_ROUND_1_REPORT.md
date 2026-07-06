# Relatório QA Round 1 — Correções aplicadas (2026-05-12)

Resposta às 7 questões A+B do relatório de QA. Os 5 itens da categoria C
(Fase 4 customizada) ficam para a próxima rodada.

## A. Correções críticas

### A.1 — Filiais "0101" → "01"
- [backend/protheus_api.py](../backend/protheus_api.py):
  - `list_branches()` normaliza `*_FILIAL` truncando para os 2 primeiros chars com dedup preservando ordem.
  - `resolve_table_name()` faz `branch = branch.strip()[:2]` antes de validar — garante que valores como `0101`, `01020001` virem sempre `01`.
- Smoke: `SE1 + 0101 + 0 = SE1010` ✅

### A.2 — Tema CSS aplicado globalmente
- [frontend/css/style.css](../frontend/css/style.css): paleta `:root` agora usa `--fx-primary` (renomeada de `--fx-green`). Aliases antigos (`--fx-green`, `--fx-blue`) viraram referências para a nova var.
- A **sidebar** agora deriva sua cor de `--fx-menu` = `color-mix(--fx-primary, black 8%)` — quando o admin escolher verde, a sidebar fica verde escura; se escolher azul, fica azul escura.
- [frontend/js/layout.js](../frontend/js/layout.js): `applyBranding()` consome `/api/settings/public` e injeta `<style id="fx-branding-override">` no `<head>` com as variáveis CSS derivadas (helpers `_darkenHex`/`_softenHex`). Função roda no boot do layout autenticado e nas páginas sem layout (login/setup).

### A.3 — Falso-positivo Redis em modo dev SQLite
- [backend/routers/admin_routes.py](../backend/routers/admin_routes.py) — `health_detail` agora detecta `sqla+`/`db+` no broker URL e reporta `type: "sqlite"` + `mode: "sqlite-dev"` em vez de tentar ping no Redis. Componente foi renomeado de `redis` para `broker` (mais genérico).
- [frontend/js/admin.js](../frontend/js/admin.js) — card "Broker da fila" mostra ícone 💾 + badge "SQLite (modo dev)" + nota explicativa quando aplicável; ícone 🔴 + badge "Redis" quando Redis ativo (produção LXC).

## B. Melhorias de usabilidade

### B.4 — Auditor Fiscal: counter de erros + UI cancelável
- [backend/fiscal/auditor.py](../backend/fiscal/auditor.py):
  - Nova exception `AuditAborted`.
  - Contador `consecutive_errors` zera a cada XML obtido com sucesso; aborta com `AuditAborted` quando atinge `FISCAL_MAX_CONSECUTIVE_ERRORS` (default 10, configurável no Admin).
  - Stats incluem `consecutive_errors` e `aborted_by_errors`.
- [backend/queue/tasks/fiscal_task.py](../backend/queue/tasks/fiscal_task.py) — mapeia `AuditAborted` para `ERR-FISCAL-005` com mensagem amigável.
- [frontend/js/fiscal.js](../frontend/js/fiscal.js) — adicionada função `_trackAuditJob()` que abre modal de progresso (Bootstrap), polling com backoff (3→15s), barra de % real, ETA estimada, botão "Cancelar operação" e botão "Fechar (continua rodando)".

### B.5 — Mensagens amigáveis no Builder
- [backend/routers/protheus_routes.py](../backend/routers/protheus_routes.py) — `/api/protheus/columns` retorna **404** com texto explicativo quando a tabela física não existe no SQL Server (em vez de retornar `{columns: []}`).
- [frontend/js/protheus.js](../frontend/js/protheus.js) — `loadColumns()` agora distingue 3 cenários:
  - "tabela não existe" → alerta amarelo explicando que o Protheus cria a tabela na primeira escrita + sugestão de checar a filial.
  - "sem permissão" → alerta vermelho com instrução para falar com o admin.
  - "erro genérico" → mostra a mensagem técnica.

### B.6 — Admin amigável + editor de Wizard
**Reescrita do painel** [frontend/pages/admin.html](../frontend/pages/admin.html) + [frontend/js/admin.js](../frontend/js/admin.js) em **4 abas**:
1. **📊 Status** — cards com ícones (Protheus, Broker, Scheduler, Jobs), métricas formatadas para leigos.
2. **⚙️ Configurações** — sub-abas (Identidade / Banco / SMTP / APIs / Auditor Fiscal). Permite editar tudo que entrou no Wizard sem entrar em arquivos.
3. **📖 Catálogo de Erros** — renderiza o markdown direto (busca via Ctrl+F).
4. **🛠️ Manutenção** — reload + restart com descrição clara.

**Novos endpoints em [backend/routers/admin_routes.py](../backend/routers/admin_routes.py):**

| Método | Path | Função |
|---|---|---|
| GET | `/api/admin/config` | Snapshot mascarado (sem secrets) |
| POST | `/api/admin/config/branding` | Nome + cor primária |
| POST | `/api/admin/config/branding/logo` | Upload de logo |
| POST | `/api/admin/config/db` | Atualiza URL + reseta engine |
| POST | `/api/admin/test/db` | Testa URL sem persistir |
| POST | `/api/admin/config/smtp` | SMTP — senha vazia mantém atual |
| POST | `/api/admin/test/smtp` | Envia e-mail de teste |
| POST | `/api/admin/config/apis` | TSS/Smartlink/NFSTOCK |
| POST | `/api/admin/config/fiscal` | FISCAL_SOURCE / NOTIFY_EMAIL / AUTO_BRANCHES / MAX_CONSECUTIVE_ERRORS |

### B.7 — Catálogo de Erros na UI
- [backend/routers/admin_routes.py](../backend/routers/admin_routes.py) — `GET /api/admin/error-catalog` serve o conteúdo bruto de [docs/ERROR_CATALOG.md](ERROR_CATALOG.md).
- [frontend/js/admin.js](../frontend/js/admin.js) — renderizador markdown próprio (sem CDN externa) cobre tabelas, títulos, listas, code blocks, negrito. Carregado lazy ao abrir a aba.

## Smoke test passou

```
A.1 SE1 + 0101 + 0    = SE1010
A.1 SE1 + 01 + 0      = SE1010
A.1 SE1 + 01020001 + 0 = SE1010
A.3 broker mode: sqlite-dev
B.4 AuditAborted: pronto
B.6 admin endpoints: 13
total rotas: 71
```

## Próximo passo — Fase 4 customizada (C.8 a C.12)

| # | Feature | Estimativa |
|---|---|---|
| C.8 | JOINs múltiplos no Builder Visual | 1-2 dias |
| C.9 | Categorização de tabelas (módulos) | 0.5 dia |
| C.10 | Grupos de e-mail para Auditor Fiscal | 0.5 dia |
| C.11 | E-mail de boas-vindas + bloqueio 5 tentativas | 0.5 dia |
| C.12 | Dicionário de dados SX2/SX3 | 1-2 dias |

Sugestão de ordem: **C.9 → C.10 → C.11 → C.12 → C.8** (mais simples para mais complexo;
C.8 fica por último porque depende de UI bem testada).

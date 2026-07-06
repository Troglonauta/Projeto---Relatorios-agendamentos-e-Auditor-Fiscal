# Sprint 22 — v2.12.0 (2026-06-13)

Leva de melhorias de UX + RBAC solicitada pela Fertimaxi. Aplicado no repo local
e no servidor de produção (192.168.0.219).

## 22.1 — Dark mode (selects/listas que ficavam brancos)
- **Causa raiz:** `background:#fafbfc/#fffbe6` **inline** em modais + Select2 sem
  override dark (o tema só cobria Choices.js).
- **Correções (`frontend/css/style.css`):** novas classes `.soft-panel` e
  `.soft-warn` (adaptam ao tema) + bloco completo de dark mode para **Select2**
  (`.select2-*`). Removidos os `background:` inline em `users.js` (Perfis
  vinculados), `schedules.js` (Periodicidade) e `fiscal.js` (aviso de chave),
  trocados pelas classes.

## 22.2 — Ícone de senha (macaquinho 🙈)
- O código já usava SVG; o 🙈 era o `pwd-toggle.js` **antigo em cache** do
  navegador (cacheado na janela em que `/static` tinha cache de 7 dias).
- Limpado o emoji do comentário. `/static` serve `no-cache`/ETag, então um
  **hard refresh (Ctrl+Shift+R) uma vez** resolve em qualquer máquina afetada;
  novos acessos já vêm corretos.

## 22.3 — Paginação DataTables (em cima E embaixo)
- **Auditor (`fiscal.js`):** adicionado `dom` com paginação no topo e no rodapé.
- **Consultas (`protheus.js`):** o grid de resultados agora é DataTables (mesmo
  padrão/idioma do Auditor), com paginação topo+rodapé. `protheus.html` passa a
  carregar DataTables 2.1.8 (após o jQuery já existente). Indicador de paginação
  server-side antigo ocultado.

## 22.4 — Auditor Fiscal liberável ao operador (RBAC)
- Nova ação **`fiscal`** (`schemas.ActionLiteral` + `models.Action.FISCAL`).
- `fiscal_routes.py`: todas as rotas passam de `require_admin` para
  `require_action("fiscal")` — **admin sempre tem; operador só se concedida**.
- `layout.js`: menu "Auditor Fiscal" passa de `admin:true` para `action:"fiscal"`.
- `fiscal.js`: guard de entrada aceita operador com a ação.
- `users.js`: checkbox **"Auditor Fiscal"** nas Ações permitidas do usuário.
- Governança: ações concedidas explicitamente (least-privilege) e o purge
  continua auditado em `AuditLog` (`fiscal.purge`).

## 22.5 — Removida a função "Visualizar Amostra"
- Removido o botão (olho azul) e o handler em `protheus.js`. Sem referências
  órfãs (`btnPreviewEmail` do Admin é outro botão, mantido).

## Deploy / versão
- **v2.12.0** (build 2026-06-13). Import-check no venv do servidor ANTES do
  restart (aborta se quebrar). Serviços reiniciados, `/health` 200.
- Arquivos: `backend/routers/fiscal_routes.py`, `backend/schemas.py`,
  `backend/models.py`, `backend/version.py`, `frontend/js/{pwd-toggle,users,
  schedules,fiscal,protheus,layout}.js`, `frontend/css/style.css`,
  `frontend/pages/protheus.html`.

## Sugestões de melhoria (próximas)
1. **Bundle/host próprio dos assets** (jQuery/Select2/DataTables/Bootstrap) em
   vez de CDN: funciona offline/intranet, evita dependência externa e melhora
   segurança (sem 3rd-party em runtime). Resolve de vez o cache de frontend
   (filenames com hash de versão).
2. **CSP + Subresource Integrity** nos `<script>` externos enquanto usar CDN.
3. **Auditoria de acesso ao Auditor**: logar quando operador abre o Auditor.
4. **Paginação do Consultas**: avaliar unificar server-side vs client-side
   (hoje é híbrido — "Tamanho" busca no servidor, DataTables pagina no cliente).

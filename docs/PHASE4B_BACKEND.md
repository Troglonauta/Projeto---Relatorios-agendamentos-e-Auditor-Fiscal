# Sprint 4.B — Backend (TOTVS Transmite + cruzamento 8 tabelas)

Entrega: 2026-05-14. Frontend dessa sprint fica para a próxima rodada.

## Pivô concluído: A1 → Transmite

| Antes | Agora |
|---|---|
| `A1CertSource` (PFX + SOAP SEFAZ) | **`TransmiteSource`** (REST `transmite.totvs.app` + JWT) |
| Gerencia certificado, validade UF, endpoint por estado | Apenas URL + user + senha. TOTVS faz o resto. |

`A1CertSource` continua como stub (sem deletar para não quebrar settings antigos).
`FISCAL_SOURCE=transmite` é o default novo no factory.

## TransmiteSource — pontos-chave

- **Login**: `POST {url}/api/auth/login` → recebe JWT com `expires_in`.
- **Cache de token thread-safe** (Lock global): TTL = `min(expires_in_servidor, TRANSMITE_TOKEN_CACHE_SECONDS)`. Default 7h.
- **3 rotas candidatas** para o XML (`/api/nfe/{chave}/xml`, `/api/documentos?chave=...`, `/api/nfe/{chave}/download`) — usa a primeira que responde.
- **Retry 401**: token revogado pelo servidor → re-login automático (1 retry).
- **Parsing flexível**: aceita XML direto OU JSON com `xml` ou `xml_base64`.

## Cruzamento 8 tabelas — batch loading

`_load_period_with_extras(engine, branch, date_from, date_to)` faz **9 queries fixas**:

| Tabela | Conteúdo | Query |
|---|---|---|
| SF1 | Cabeçalho NFe entrada | 1× WHERE F1_EMISSAO BETWEEN ... |
| SD1 | Itens NFe entrada | 1× WHERE D1_DOC IN (...) |
| SDT | Itens internalização XML | 1× WHERE DT_CHVNFE IN (...) |
| CKOCOL | Controle coleta | 1× WHERE CK0_CHVNFE IN (...) — falha silenciosa se tabela ausente |
| SDE | Centro de custo/rateio | 1× WHERE DE_DOC IN (...) |
| SE2 | Títulos a pagar | 1× WHERE E2_NUM IN (...) |
| SFT | Livros fiscais itens | 1× WHERE FT_NFISCAL IN (...) |
| SF3 | Livros fiscais header | 1× WHERE F3_NFISCAL IN (...) |
| SB1 | Cadastro produto (NCM) | 1× WHERE B1_COD IN (...) — códigos coletados de SD1 |

**Performance**: para auditar 500 NFe no período, são **9 queries** em vez de 4.500 (500 × 9).

## Comparators novos

Configurados em `backend/fiscal/comparators.py`. **Tolerância** vem do `settings_store`:

| Setting | Default | Cobre |
|---|---|---|
| `FISCAL_TOLERANCE_VALOR_RS` | `0.05` | Valores totais, rateio, duplicatas |
| `FISCAL_TOLERANCE_ICMS_RS` | `0.02` | Base/valor ICMS |
| `FISCAL_TOLERANCE_QUANT` | `0.01` | Quantidades |
| `FISCAL_VALIDATE_NCM` | `true` | Liga/desliga validação NCM |

| Comparator | Severidade | Tolerância | Quando dispara |
|---|---|---|---|
| `compare_ncm` | **critical sempre** | **nenhuma** | NCM XML ≠ SB1.B1_POSIPI (digit-only, ignora `8473.30.41` vs `84733041`) |
| `compare_cfop` | critical | nenhuma | D1_CF ≠ XML/det/prod/CFOP |
| `compare_xml_internalized` | critical (item ausente em SDT) / warn (item extra) | — | Itens XML × SDT |
| `compare_collection` | warn | — | NFe sem registro em CKOCOL |
| `compare_cost_center` | warn/critical | R$ 0,05 | Σ DE_VALOR ≠ D1_TOTAL do item |
| `compare_titles_total` | warn/critical | R$ 0,05 | Σ SE2.E2_VALOR ≠ F1_VALBRUT |
| `compare_titles_vs_xml` | warn | R$ 0,05 | Cada duplicata SE2 × cobr/dup do XML |
| `compare_fiscal_items` | warn/critical | R$ 0,02 (ICMS) | SFT × XML por item (CFOP, base ICMS, valor ICMS) |
| `compare_fiscal_header` | warn/critical | R$ 0,02 (ICMS) | SF3 × ICMSTot do XML |

Severidade auto-escalonada em valores monetários:
- `|diff| <= tol` → ignora
- `tol < |diff| <= R$ 1.00` → warn
- `|diff| > R$ 1.00` → critical

## ERR-FISCAL-006 (novo)

| Código | Mensagem | HTTP | Causa | Resolução |
|---|---|---|---|---|
| ERR-FISCAL-006 | NCM divergente entre XML e cadastro SB1 (compliance) | 422 | NCM XML ≠ SB1.B1_POSIPI | Corrigir cadastro **antes do fechamento fiscal** |

## E-mail com seção NCM destacada

Quando há ≥ 1 anomalia de NCM no batch, o template HTML ganha um bloco **vermelho no topo**:
- Header `⚠ COMPLIANCE FISCAL — ERR-FISCAL-006`
- Contagem de divergências
- Tabela com NFe (8 últimos dígitos) + filial + NCM Protheus + NCM XML
- Mensagem clara: "resolva ANTES do fechamento fiscal do mês"

Render em `_build_ncm_section()` (em `auditor.py`). O placeholder `{{NCM_SECTION}}` no template fica vazio se não há NCM.

## 3 ajustes UX entregues junto (operadores)

### Ajuste 1 — Dropdown limpo
[frontend/js/protheus.js](../frontend/js/protheus.js) — `renderAliasOptions()` agora checa `auth.isAdmin()`:
- **Admin**: `SE1 — Contas a Receber · [FINANCEIRO,CONTROLADORIA]`
- **Operador**: `SE1 — Contas a Receber`

### Ajuste 2 — Dashboard só admin
- [layout.js](../frontend/js/layout.js): item "Dashboard" ganhou `admin: true`.
- Novo helper `api.js::landingPage()` retorna `dashboard.html` (admin) ou `protheus.html` (operador).
- [login.js](../frontend/js/login.js) e [change-password.html](../frontend/pages/change-password.html) usam `landingPage()` no redirect.
- [dashboard.js](../frontend/js/dashboard.js) tem guard `if (!auth.isAdmin()) → protheus.html` (defesa em profundidade).

### Ajuste 3 — Limite de 3 sessões simultâneas

**Backend:**
- Novo model `ActiveSession` em [models.py](../backend/models.py): `jti, user_id, expires_at, revoked_at, ip_address, user_agent`.
- [auth.py::create_access_token](../backend/auth.py) gera `jti` UUID e devolve `(token, jti, exp)`.
- [auth_routes.login](../backend/routers/auth_routes.py):
  1. Verifica credenciais
  2. Purga sessões expiradas/revogadas do user
  3. Conta sessões ativas; se ≥ `MAX_CONCURRENT_SESSIONS` (default 3), retorna **429** com mensagem amigável.
  4. Persiste nova sessão.
- [auth_routes.logout](../backend/routers/auth_routes.py) novo endpoint `POST /api/auth/logout` revoga `jti` atual (libera slot).
- [deps.get_current_user](../backend/deps.py): valida que `jti` está ativo na tabela; senão 401.

**Frontend:**
- [api.js::logout](../frontend/js/api.js) chama `POST /api/auth/logout` antes de limpar localStorage.
- [login.js](../frontend/js/login.js) destaca 429 como `warning` (amarelo) em vez de `danger` (vermelho).
- Mensagem em `?reason=revoked` adicionada.

Configurável via `AppSetting('MAX_CONCURRENT_SESSIONS')`.

## Backup pré-4.B

Snapshot completo do estado v1.4.0 (pré Sprint 4.B) em `backup/v1.4.0-sprint-4A/snapshot.tar.gz`.
Instruções de rollback em `backup/v1.4.0-sprint-4A/README.md`.

## Smoke test passou

```
NCM iguais (com máscara): None  ← 8473.30.41 ≡ 84733041
NCM diferentes: critical (compliance)
Total dif R$ 0,04 (dentro tol): None
Total dif R$ 5,00: critical
ERR-FISCAL-006: {error_code, message, detail}
Collection ausente: warn (CKOCOL não bateu)
total de rotas: 82 (era 81)
códigos no catálogo: 33 (era 32)
```

## Próxima rodada — Sprint 4.B Frontend

| Item | Notas |
|---|---|
| Bloco "TOTVS Transmite" no Wizard + Admin > APIs externas | Já temos `transmite_*` keys no admin payload — só falta a UI |
| Indicador no fiscal.html dizendo "fonte ativa: Transmite" | Pequeno selo no topo da página |
| Detalhes do anomaly tooltip: mostrar tolerância aplicada | `compare_total` retorna tol no `note` — front renderiza |
| Filtro novo na lista de anomalias: "só NCM" | Toggle/badge para vermelho destacado |
| Visualização do template HTML antes do envio | Botão "Preview e-mail" no Admin > Auditor Fiscal |

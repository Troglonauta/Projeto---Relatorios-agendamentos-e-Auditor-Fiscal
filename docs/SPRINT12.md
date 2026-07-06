# Sprint 12 — Auditoria Interna Protheus × Protheus

**v2.4.0 · 2026-05-26 · codename `internal-audit`**

Pivô arquitetural: o Auditor Fiscal abandona qualquer fonte externa de XML
(Alterdata NFStock, SEFAZ A1, TOTVS Transmite/TSS) e passa a operar
**100% dentro do Protheus**, cruzando o XML que o ERP já internaliza
durante o recebimento (SDS/SDT) contra a nota classificada (SF1/SD1).

> Resultado: zero chamadas HTTP, zero `XmlNotFound`, zero token, zero
> certificado A1. Auditoria determinística via SQL com um único LEFT JOIN.

## 1. Motivação

- A API MS-Exportação da Alterdata seguia retornando HTML 200 mascarando
  404 (193 pendentes em produção mesmo após Sprint 11 Hotfix).
- O ERP da Fertimaxi já recebe e internaliza os XMLs em **SDS** (cabeçalho)
  e **SDT** (itens) na entrada de mercadoria. Essa é a fonte de verdade real.
- LEFT JOIN entre SDS e SF1 detecta naturalmente o cenário crítico que mais
  importa: **"XML chegou no ERP mas a nota não foi classificada"**.

## 2. Novo motor — `backend/fiscal/internal_audit.py`

`_load_audit_period_internal(db_engine, branch, df, dt, *, chave_filter=None)`
faz 1 query principal + 2 batch:

```sql
-- 1) SDS LEFT JOIN SF1 no periodo
SELECT sds.DS_*, sf1.F1_*
FROM SDS{branch}{suffix} sds WITH (NOLOCK)
LEFT JOIN SF1{branch}{suffix} sf1 WITH (NOLOCK)
  ON sf1.F1_CHVNFE = sds.DS_CHAVENF AND sf1.D_E_L_E_T_ = ' '
WHERE sds.D_E_L_E_T_ = ' '
  AND sds.DS_EMISSAO BETWEEN :df AND :dt
ORDER BY sds.DS_EMISSAO, sds.DS_CHAVENF;

-- 2) SDT (itens XML internalizado) por DS_CHAVENF
SELECT DT_* FROM SDT{branch}{suffix} WITH (NOLOCK)
WHERE D_E_L_E_T_ = ' ' AND DT_CHAVENF IN (...);

-- 3) SD1 (itens da nota classificada) por F1_DOC
SELECT D1_* FROM SD1{branch}{suffix} WITH (NOLOCK)
WHERE D_E_L_E_T_ = ' ' AND D1_DOC IN (...);
```

Saída: lista de documentos no formato

```python
{
  "chave":     "44 digitos",
  "branch":    "01",
  "sds":       { DS_*: valor, ... },          # cabecalho XML interno
  "sf1":       { F1_*: valor, ... } | None,   # None => Nota Ausente
  "sdt_items": [ {DT_*: valor, ...}, ... ],
  "sd1_items": [ {D1_*: valor, ...}, ... ],
}
```

## 3. `FiscalRuleEngine` — 17 regras determinísticas

`backend/fiscal/rule_engine.py` consome o doc unificado:

### Cabeçalho (SDS × SF1)

| # | Regra | Lado SF1 | Lado SDS | Severidade |
|---|---|---|---|---|
| R0 | Nota Ausente (XML sem F1) | `(nao classificada)` | `DS_NUMNF/DS_SERIE` | **critical** |
| R1 | Número | `F1_DOC` | `DS_NUMNF` | critical |
| R2 | Série | `F1_SERIE` | `DS_SERIE` | warn |
| R3 | Data emissão | `F1_EMISSAO` | `DS_EMISSAO` | warn |
| R4 | Valor total | `F1_VALBRUT` | `DS_TOTAL` | critical |
| R5 | Base ICMS | `F1_BASEICM` | `DS_BASEICM` | critical |
| R6 | Valor ICMS | `F1_VALICM` | `DS_VALICM` | critical |
| R7 | Frete + Seguro | `F1_FRETE+F1_SEGURO` | `DS_FRETE+DS_SEGURO` | warn |
| R8 | Desconto | `F1_DESCONT` | `DS_DESC` | warn |
| R9 | Outras despesas | `F1_DESPESA` | `DS_OUTRO` | warn |

### Itens (SDT × SD1, alinhados por número do item)

| # | Regra | Lado SD1 | Lado SDT | Severidade |
|---|---|---|---|---|
| R10 | Quantidade | `D1_QUANT` | `DT_QUANT` | warn |
| R11 | Valor unitário | `D1_VUNIT` | `DT_VUNIT` | warn |
| R12 | Valor total item | `D1_TOTAL` | `DT_TOTAL` | warn |
| R13 | NCM | `D1_BM` | `DT_NCM` | **critical** |
| R14 | CFOP | `D1_CF` | `DT_CFOP` | critical |
| R15 | CST/CSOSN | `D1_CLASFIS` | `DT_CST` | critical |
| R16 | ICMS tríplice (alíquota+valor+base) | `D1_PICM/VALICM/BASEICM` | `DT_PICM/VICMS/BASEICM` | critical |

Tolerâncias (todas configuráveis via `AppSetting`):

| Setting | Default |
|---|---|
| `FISCAL_TOLERANCE_VALOR_RS` | R$ 0,05 |
| `FISCAL_TOLERANCE_ICMS_RS`  | R$ 0,02 |
| `FISCAL_TOLERANCE_QUANT`    | 0,01 |

## 4. Limpeza de código (Clean Code)

### Backend
- **Deletado:** `backend/fiscal/xml_sources/` inteiro
  (`__init__.py`, `base.py`, `nfstock.py`, `a1.py`, `transmite.py`, `tss.py`).
- **Deletado:** `lxml` import e `_parse_nfe_*` (header/items/totals/duplicatas)
  em `auditor.py`. O motor não parseia XML — consome o que já está em SDT/SDS.
- **Deletado:** `_load_period_with_extras` + helpers `_exec_in*` em `auditor.py`
  (substituídos por `internal_audit._load_audit_period_internal`).
- **Deletado:** `AuditAborted`, `consecutive_errors`, `FISCAL_MAX_CONSECUTIVE_ERRORS`
  (sem rede = sem falhas em sequência).
- **Slimmed:** `comparators.py` reduzido para apenas `Divergence`, `tol_*`
  e helpers de normalização. As 11 funções legadas (compare_cnpj, compare_chave,
  compare_xml_internalized, etc.) saíram.
- **Endpoints removidos:**
  - `POST /api/fiscal/config/test-source`
  - `POST /api/fiscal/source/switch`
  - `GET  /api/fiscal/source-info` (substituído por `GET /api/fiscal/engine-info`)
  - `POST /api/admin/config/apis`, `POST /api/admin/test/nfstock`,
    `POST /api/admin/test/a1`, `POST /api/admin/config/a1`,
    `POST /api/admin/config/a1/upload`
  - `POST /api/setup/apis`, `POST /api/setup/fiscal-source`
- **Settings removidos do snapshot** (`GET /api/admin/config`): toda a seção
  `apis` (NFStock/Transmite/TSS) e as chaves A1 dentro de `fiscal`.
- **Severidade `pending` aposentada** — não existem mais XMLs "pendentes
  na fonte" porque não há mais fonte externa.

### Frontend
- **`frontend/js/fiscal.js`** — removidos: banner `sourceInfo`, função
  `loadSourceInfo()`, botão "Testar fonte XML", `testModal`, aba "Pendentes
  na Fonte", inputs `hora_from/hora_to`. Banner novo `engineInfo` mostra
  só as tolerâncias.
- **`frontend/js/admin.js`** — aba "APIs externas" removida; bloco "Fonte
  de XML Ativa" no Fiscal substituído por alerta informativo do motor
  interno; handlers `btnSaveApis/btnTestNfs/btnA1Upload/btnSaveA1/btnTestA1`
  + helper `_updateSourceHint` deletados.
- **`frontend/js/setup.js` + `setup.html`** — passo "APIs externas"
  removido do Wizard. De 6 passos para 5 (Branding → DB → SMTP → Admin → Finalizar).

### Migração / compatibilidade

- Settings antigos no banco (`NFSTOCK_*`, `FISCAL_A1_*`, `TRANSMITE_*`,
  `FISCAL_TSS_*`, `SMARTLINK_*`, `FISCAL_SOURCE`, `FISCAL_MAX_CONSECUTIVE_ERRORS`)
  permanecem no `AppSetting` mas não são lidos — não quebra instalações
  pré-2.4 que ainda os tenham. Próxima limpeza de banco pode removê-los.
- O modelo `FiscalAnomaly` segue o mesmo schema. Anomalias antigas continuam
  acessíveis; apenas não se geram mais com `severity='pending'`.

## 5. Verificação smoke

```
python -c "from backend.fiscal.internal_audit import _load_audit_period_internal; print('OK')"
python -c "from backend.fiscal.rule_engine import FiscalRuleEngine; e = FiscalRuleEngine({'sds':{}, 'sf1':None, 'chave':'x'*44, 'branch':'01'}); print(e.run())"
python -c "from backend.fiscal.auditor import run_audit; print('OK')"
python -c "from backend.main import app; print('OK')"
```

`xml_sources/` deletado:

```
ls backend/fiscal/
  __init__.py  auditor.py  comparators.py  internal_audit.py
  rule_engine.py  templates  webhook.py
```

## 6. Próximos passos sugeridos

- Migração SQL para deletar settings legados (`NFSTOCK_*`, `FISCAL_A1_*`,
  `TRANSMITE_*`, `FISCAL_TSS_*`, `SMARTLINK_*`, `FISCAL_SOURCE`) após algumas
  semanas em produção.
- Avaliar adicionar SA2 ao loader interno para incluir comparação de CNPJ
  do fornecedor (`DS_CNPJ` × `SA2.A2_CGC` via `F1_FORNECE+F1_LOJA`).
- Documentar no README quais releases Protheus expõem as colunas
  `DT_PICM/DT_VICMS/DT_CST` em SDT (algumas releases antigas precisam de
  customização do dicionário).

# Sprint 4.B Frontend + Correção SF1 — 2026-05-14

## 🐛 Correção crítica: `[SQL Server] Invalid column name 'F1_NFEFOR'`

A query do `SF1` em `auditor.py::_load_period_with_extras` listava `F1_NFEFOR`,
campo que **não existe** no dicionário Fertimaxi. Matava o worker antes de
processar o primeiro documento.

### Dicionário oficial agora hardcoded em [`auditor.py`](../backend/fiscal/auditor.py)

```python
SF1_COLS = "F1_FILIAL, F1_DOC, F1_SERIE, F1_FORNECE, F1_LOJA, F1_EMISSAO, F1_VALBRUT, F1_CHVNFE"
SD1_COLS = ("D1_FILIAL, D1_DOC, D1_SERIE, D1_FORNECE, D1_LOJA, "
            "D1_ITEM, D1_COD, D1_QUANT, D1_VUNIT, D1_TOTAL, "
            "D1_VALICM, D1_VALIPI, D1_TES, D1_CF")
SA2_COLS = "A2_FILIAL, A2_COD, A2_LOJA, A2_NOME, A2_CGC"
SB1_COLS = "B1_COD, B1_DESC, B1_POSIPI"
SE2_COLS = ("E2_FILIAL, E2_PREFIXO, E2_NUM, E2_FORNECE, E2_LOJA, "
            "E2_PARCELA, E2_VALOR, E2_EMISSAO, E2_VENCREA")
```

Para SDT/SDE/SFT/SF3/CKOCOL continua `SELECT *` (cliente disse "use colunas padrão").
Se aparecer "Invalid column name" em alguma dessas, atualizar a constante correspondente.

### Como o CNPJ do fornecedor é obtido agora

Antes: `F1_NFEFOR` (inexistente).
Agora: **batch loader `SA2`** carrega `A2_CGC` por `(A2_COD, A2_LOJA)` para todos os
fornecedores que aparecem no SF1 do período.

`_compare_doc` pega de `extras['sa2']['A2_CGC']`. Quando o cadastro está incompleto,
o comparador silenciosamente pula (não inventa diferença falsa).

### Novo helper `_exec_in_cols`

Use para tabelas com dicionário oficial (SF1, SD1, SA2, SB1, SE2) — força a lista
de colunas. Para o resto, continua `_exec_in` (com `SELECT *`).

---

## 🆕 Sprint 4.B Frontend — 5 itens entregues

### 1. Bloco TOTVS Transmite no Wizard

[`frontend/pages/setup.html`](../frontend/pages/setup.html) — passo 4 (APIs externas) agora começa com:

```
📨 TOTVS Transmite (recomendado para Auditor Fiscal)  [aberto por padrão]
   - URL Transmite
   - Usuário
   - Senha
```

Outras fontes (TSS on-premise, Smartlink, NFSTOCK) ficam em `<details>` fechados.

[`frontend/js/setup.js`](../frontend/js/setup.js) envia `transmite_url/user/password` no payload.
[`backend/schemas.py`](../backend/schemas.py) `SetupApisStep` aceita esses 3 campos novos.
[`backend/routers/setup_routes.py`](../backend/routers/setup_routes.py) grava como `TRANSMITE_URL/USER/PASSWORD`
e — **bônus** — se as 3 forem preenchidas, define `FISCAL_SOURCE=transmite` automaticamente.

### 2. Bloco TOTVS Transmite no Admin > APIs externas

[`frontend/js/admin.js`](../frontend/js/admin.js) — aba **Configurações > APIs externas**
agora tem o bloco Transmite **no topo**, com borda verde à esquerda e badge "recomendado":

```
📨 TOTVS Transmite  [recomendado]
   ↳ URL, Usuário, Senha (vazio = manter)
```

Submit reutiliza o endpoint admin existente; senhas vazias preservam o valor atual.

### 3. Indicador "Fonte Ativa" no topo do Auditor

Novo endpoint **`GET /api/fiscal/source-info`** ([`fiscal_routes.py`](../backend/routers/fiscal_routes.py)):

```json
{
  "active_source": "transmite",
  "active_source_label": "TOTVS Transmite",
  "configured": true,
  "tolerance": {
    "valor_rs": "0.05",
    "icms_rs": "0.02",
    "quantidade": "0.01"
  },
  "ncm_validation": true
}
```

No topo de `fiscal.html` aparece um banner verde com:
```
📨 Fonte ativa: TOTVS Transmite  [configurada]
   Tolerância: R$ 0,05 em valores · R$ 0,02 em ICMS · 0.01 em quantidade ·
               NCM: crítico (sem tolerância)
```

CSS em [`style.css`](../frontend/css/style.css) (`.source-info-banner`).

### 4. Filtro "Só NCM" com destaque vermelho

Painel de filtros ganhou switch `🚨 compliance` ([`fiscal.js`](../frontend/js/fiscal.js)):
- Quando ligado, força `severity=critical` na query e filtra `field_compared` com `/ncm/i` no cliente.
- Linhas com NCM divergente recebem classe `.ncm-row` (fundo `#fff3f3` + borda vermelha à esquerda).
- Campo aparece em vermelho com 🚨 + `<strong>`.
- Mensagem específica quando filtra e não encontra: "🎉 compliance OK".

### 5. Tooltip de tolerância nas anomalies

`fiscal.js::_tooltipFor(field)` mapeia o campo para a tolerância aplicada:

| Campo contém | Tolerância exibida |
|---|---|
| `valor_total`, `se2_parcela`, `titulos_total`, `rateio` | R$ 0,05 (configurável) |
| `base_icms`, `valor_icms`, `sft_total_icms`, `sf3` | R$ 0,02 (configurável) |
| `quantidade` | 0.01 |
| `ncm`, `cfop`, `cnpj_fornecedor`, `chave_acesso` | **0 (sem tolerância)** |

`<tr title="..."` na lista. Hover mostra "Tolerância aplicada: R$ 0,05 — Campo técnico: item_001_valor_total".

### 6. Botão "Preview e-mail" no Admin > Auditor Fiscal

Novo endpoint **`GET /api/admin/email/preview/fiscal`** que renderiza o template HTML
com **dados MOCK** (incluindo NCM divergente para mostrar o bloco vermelho de compliance).

[`admin.js`](../frontend/js/admin.js):
- Botão `📧 Preview e-mail de anomalias` na sub-aba **Auditor Fiscal**.
- Abre modal grande (880 px) com `<iframe>` exibindo o HTML.
- `srcdoc` evita problema de JWT em cookies (carregamos via fetch com Authorization
  header, depois injetamos no iframe).

---

## Smoke test passou

```
OK imports
OK colunas oficiais (F1_NFEFOR expurgado de SF1_COLS)
Endpoints novos:
   /api/admin/email/preview/fiscal
   /api/fiscal/source-info
total rotas: 84 (era 82)
```

## Backup

Snapshot pré-correção em `backup/v1.4.2-pre-sf1-fix/snapshot.tar.gz` (158 KB).

## Próximos passos sugeridos

1. **Teste end-to-end real** com Transmite homologação e período de 1 dia.
2. **Migrar UI de senhas em SMTP/APIs** para usar a mesma lógica "vazio = manter".
3. **Detalhe da anomalia ao clicar** — modal com `GET /api/fiscal/anomaly/{id}` mostrando
   protheus_value vs xml_value lado a lado.
4. **Snooze/ack de anomalias** — marcar como "ciente" para não voltar a aparecer
   no próximo batch (model `FiscalAnomaly.acknowledged_at`).

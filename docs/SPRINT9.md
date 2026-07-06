# Sprint 9 — Motor Contábil-Fiscal (`FiscalRuleEngine`)

**v2.1.0 · 2026-05-21**

15 regras de compliance fiscal organizadas em 3 lotes (cabeçalho, totais,
itens). Substitui o `_compare_doc` plano por uma classe instrumentada,
com método isolado por regra e severidade explícita.

## Arquitetura

```
auditor.py::run_audit
    └─► _compare_doc (thin wrapper, mantém retrocompat)
            └─► FiscalRuleEngine(doc, xml_header, xml_items, xml_totals, extras).run()
                    ├─► Lote 1 — Cabeçalho (5 regras)
                    ├─► Lote 2 — Totais e Impostos (5 regras)
                    ├─► Lote 3 — Itens (5 regras × N itens)
                    └─► _run_legacy_comparators (Sprint 4.B/6/8.2 — compat)
```

[`backend/fiscal/rule_engine.py`](../backend/fiscal/rule_engine.py) — classe nova.
[`backend/fiscal/auditor.py::_compare_doc`](../backend/fiscal/auditor.py) — thin wrapper de 8 linhas.
[`backend/fiscal/comparators.py`](../backend/fiscal/comparators.py) — funções puras reutilizadas pelo engine.

## Diretrizes implementadas

- **Decimal puro** — todos os valores monetários via `Decimal.quantize(0.01)` para evitar
  falsos positivos (`10.05 != 10.050001`)
- **Tolerâncias configuráveis** via `AppSetting`:
  - `FISCAL_TOLERANCE_VALOR_RS` (default R$ 0,05)
  - `FISCAL_TOLERANCE_ICMS_RS` (default R$ 0,02) — ICMS é mais restrito
  - `FISCAL_TOLERANCE_QUANT` (default 0,01)
- **Severidade explícita**:
  - `critical` — erros financeiros/tributários (multa SPED)
  - `warn` — divergências relevantes não-bloqueantes
  - `pending` (Sprint 8.2) — XML não encontrado na fonte
- **Skip silencioso** quando ambos os lados são zero/vazios (não polui o painel)
- **Método isolado por regra** — fácil testar/desabilitar individualmente

## Lote 1 — Cabeçalho (5 regras)

| # | Campo | Protheus | XML | Severidade |
|---|---|---|---|---|
| 1 | Número da NF | `SF1.F1_DOC` (sem zero-pad) | `<ide><nNF>` | **crítico** |
| 2 | Série | `SF1.F1_SERIE` | `<ide><serie>` | warn |
| 3 | Data de emissão | `SF1.F1_EMISSAO` (YYYYMMDD) | `<ide><dhEmi>` (10 chars ISO) | warn |
| 4 | CNPJ fornecedor | `SA2.A2_CGC` (JOIN F1_FORNECE+F1_LOJA) | `<emit><CNPJ>` | **crítico** |
| 5 | Valor total | `SF1.F1_VALBRUT` | `<total><ICMSTot><vNF>` | **crítico** |

> Para a regra 1, ambos os lados ganham `.strip().lstrip("0")` antes de comparar
> (Protheus armazena `'000019'`, XML retorna `'19'` — ambos viram `'19'`).

> Para a regra 3, `dhEmi` é ISO-8601 com timezone (`2026-05-15T08:30:00-03:00`);
> extraímos os 10 primeiros chars e removemos os hífens para casar com o
> formato Protheus `YYYYMMDD`. NFe 3.10 ainda usa `<dEmi>` — fallback automático.

## Lote 2 — Totais e Impostos (5 regras)

| # | Campo | Protheus | XML | Severidade |
|---|---|---|---|---|
| 6 | Base ICMS | `SF1.F1_BASEICM` | `<ICMSTot><vBC>` | **crítico** |
| 7 | Valor ICMS | `SF1.F1_VALICM` | `<ICMSTot><vICMS>` | **crítico** |
| 8 | Frete + Seguro (somados) | `SF1.F1_FRETE + F1_SEGURO` | `<vFrete> + <vSeg>` | warn |
| 9 | Desconto | `SF1.F1_DESCONT` | `<ICMSTot><vDesc>` | warn |
| 10 | Outras despesas | `SF1.F1_DESPESA` | `<ICMSTot><vOutro>` | warn |

> Lote 2 usa **tolerância ICMS** (`R$ 0,02`) para regras 6+7. Lote 2 frete/desconto/outras
> usa **tolerância valor** (`R$ 0,05`).

## Lote 3 — Itens (5 regras × N itens)

| # | Campo | Protheus | XML | Severidade |
|---|---|---|---|---|
| 11 | NCM | `SD1.D1_BM` (direto) → fallback `SB1.B1_POSIPI` | `<prod><NCM>` | **crítico** |
| 12 | CFOP | `SD1.D1_CF` | `<prod><CFOP>` | **crítico** |
| 13 | Quantidade | `SD1.D1_QUANT` | `<prod><qCom>` | crítico (>tol) |
| 14 | Valor unitário | `SD1.D1_VUNIT` | `<prod><vUnCom>` | warn |
| 15 | Valor total item | `SD1.D1_TOTAL` | `<prod><vProd>` | warn / crítico (>R$ 1,00) |

> **Regra 11 (NCM)** prioriza `D1_BM` direto na linha (release moderna do Protheus).
> Se vazio, cai para `SB1.B1_POSIPI` via JOIN por código. Compliance fiscal:
> divergência aqui SEMPRE é `critical`, sem tolerância (NCM é string de 8 dígitos exata).

> Itens presentes no Protheus mas **ausentes do XML** geram **1 anomalia única**
> (`item_N_ausente_xml` — warn), não as 5 regras explodindo individualmente.

## Schema atualizado

```python
# SF1_COLS — Sprint 9 (Lote 2)
"F1_FILIAL, F1_DOC, F1_SERIE, F1_FORNECE, F1_LOJA, F1_EMISSAO, F1_VALBRUT, F1_CHVNFE, "
"F1_BASEICM, F1_VALICM, F1_FRETE, F1_SEGURO, F1_DESCONT, F1_DESPESA"

# SD1_COLS — Sprint 9 (NCM direto)
"D1_FILIAL, D1_DOC, D1_SERIE, D1_FORNECE, D1_LOJA, D1_ITEM, D1_COD, "
"D1_QUANT, D1_VUNIT, D1_TOTAL, D1_VALICM, D1_VALIPI, D1_TES, D1_CF, D1_BM"

# XML parsing — campos novos no _parse_nfe_*
xml_header.dhemi    # ISO-8601 (NFe 4.0) ou YYYYMMDD (NFe 3.10)
xml_totals.v_frete  # <total><ICMSTot><vFrete>
xml_totals.v_seg
xml_totals.v_desc
xml_totals.v_outro
xml_totals.v_prod
```

## API pública

```python
from backend.fiscal.rule_engine import FiscalRuleEngine

engine = FiscalRuleEngine(
    doc=protheus_doc,          # dict SF1 + chave 'items' com SD1
    xml_header=xml_header,     # dict do _parse_nfe_header
    xml_items=xml_items,       # list do _parse_nfe_items
    xml_totals=xml_totals,     # dict do _parse_nfe_totals
    xml_duplicatas=xml_dups,   # list — opcional, p/ comparador SE2 legacy
    extras=extras,             # SA2, SB1, SDT, etc — via _load_period_with_extras
)
anomalies = engine.run()  # list[dict] com {field, protheus_value, xml_value, severity, note}
```

Cada anomalia tem o formato exato esperado pelo `_save_anomalies` → tabela
`fiscal_anomalies`.

## Adicionar uma nova regra (16+)

1. Definir `_check_<nome>(self)` (ou `_check_<nome>(self, item, xi)` se for por item)
2. Chamar `self._add(field, protheus_value, xml_value, severity, note)`
3. Adicionar a chamada em `run()` no lote apropriado
4. Reusar `self._norm_decimal`, `self._norm_digits`, `self._norm_string`, `self._norm_item_n`

## Smoke test end-to-end

```
--- XML extracted (NFe 4.0 dhEmi com timezone) ---
dhemi: 2026-05-15T08:30:00-03:00
totals: {v_bc_icms, v_icms, v_frete, v_seg, v_desc, v_outro, v_prod, v_nf}

--- Cenário OK (Protheus == XML em tudo) ---
anomalias das 15 regras Sprint 9: 0 ✓
3 anomalias legacy (SDT/CKOCOL sem mock) — esperado, não bloqueante

--- Cenário BAD (10 divergências cabeçalho/totais + 5 por item) ---
anomalias: 18 (15 da Sprint 9 + 3 legacy)
Lote 1: numero_nota, serie, data_emissao, cnpj_fornecedor, valor_total ✓ (5/5)
Lote 2: base_icms, valor_icms, frete_seguro, desconto, outras_despesas ✓ (5/5)
Lote 3 item 1: ncm, cfop, quantidade, valor_unit, valor_total ✓ (5/5)
criticals: 11/18 (todos os esperados — número, CNPJ, valor total, base/valor ICMS, NCM)

Security audit: 0 CRITICAL · 0 HIGH · 0 MEDIUM → CONFORMIDADE
```

## Como validar em produção

1. Rode auditoria sobre 1 dia conhecido com pelo menos 1 nota.
2. Painel "Auditor Fiscal > Anomalias" deve mostrar:
   - **Crítica**: número/CNPJ/valor total/ICMS/NCM divergente
   - **Aviso**: série/data/frete/desconto/CFOP/quantidade/valor unit divergente
3. Exporte XLSX (Sprint 8.2): aba "Anomalias Encontradas" tem 15 colunas com
   campo legível (ex: `item_1_ncm`, `base_icms`).
4. Webhook (Sprint 8.3): canal recebe alerta se ≥1 crítica.

## ⏭️ Pós Sprint 9

- **Regra 16**: CEST por item (`<prod><CEST>` × `SB1.B1_CEST`) — autuação SPED comum
- **Regra 17**: CST PIS/COFINS — hoje só ICMS é validado
- **Regra 18**: validar `dEntrada` (data de entrada no estoque) vs `dhEmi` (Protheus pode atrasar)
- **Regra 19**: Cross-check soma dos `<vProd>` dos itens contra `<vProd>` em `<ICMSTot>`
  (catch arredondamento residual entre item e cabeçalho)
- Testes unitários do `FiscalRuleEngine` (uma classe por regra) — agora possível com a
  refatoração; antes do `_compare_doc` plano era difícil testar cada regra isolada.

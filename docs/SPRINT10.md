# Sprint 10 — Lote 4 do Fiscal Rule Engine + UI Polish

**v2.2.0 · 2026-05-22 · codename `rule-engine-full`**

Fechamento das regras de compliance fiscal: **4 regras finais (16-19) + 2 bonus
(CEST, CST PIS/COFINS)**. UI Polish já entregue em Hotfix2 confirmada estável
e novo botão "Limpar Resultados" no Auditor.

## FRENTE 1 — Fiscal Rule Engine (Lote 4)

### Regra 16 — Descrição do produto (warn)
- **Protheus**: `SDT.DT_DESC` (prioritário — internalização do XML) → fallback `SD1.D1_DESC`
- **XML**: `<prod><xProd>`
- **Match tolerante**: normaliza (UPPER, sem acento, whitespace único) e usa
  `difflib.SequenceMatcher` — divergência só se ratio < 75%
- Cobre variações naturais de espaçamento/abreviação sem falso positivo

### Regra 17 — CST/CSOSN (crítico)
- **Protheus**: `SD1.D1_CLASFIS`
- **XML**: `<imposto><ICMS>*<CST>` (regime normal) OU `<CSOSN>` (Simples Nacional)
- Comparação só por dígitos (`'000' == ' 0 0 0 '`)
- Severity `critical` — código de situação tributária errado gera multa SPED

### Regra 18 — ICMS Tríplice por item (crítico × 3)
Aciona **3 anomalias separadas** por item, cada uma com severity `critical`:

| Sub-regra | Protheus | XML | Tolerância |
|---|---|---|---|
| 18a `_aliquota` | `D1_PICM` | `<pICMS>` | 0,01% |
| 18b `_valor` | `D1_VALICM` | `<vICMS>` | R$ 0,02 (tol_icms) |
| 18c `_base` | `D1_BASEICM` | `<vBC>` | R$ 0,02 (tol_icms) |

Field names: `item_N_icms_aliquota`, `item_N_icms_valor`, `item_N_icms_base`.

Skip silencioso quando ambos os lados são zero (operação isenta).

### Regra 19 — Informações Complementares (warn)
- **Protheus**: `SF1.F1_MENNOTA`
- **XML**: `<infAdic><infCpl>`
- **Match parcial inteligente**:
  1. Normaliza ambos (lowercase, sem acento, espaço único)
  2. Quebra Protheus em tokens alfanuméricos com 4+ chars
  3. Conta quantos tokens aparecem no XML
  4. **Divergência se < 70% dos tokens encontrados**
- Cobre o caso "informações copiadas com pequenas variações" sem disparar
  por quebras de linha ou ordem diferente

### Bonus — CEST (warn)
- **Protheus**: `SB1.B1_CEST`
- **XML**: `<prod><CEST>`
- Crítico para produtos sob Substituição Tributária — divergência gera autuação SPED
- Skip se um dos lados não tem CEST (operação sem ST)

### Bonus — CST PIS/COFINS (info)
- Heurística: se item tem ICMS > 0 mas o CST PIS/COFINS está marcado como
  isento (codigos `04-09, 49+`), registra **info** (não bloqueante)
- Captura erros de configuração comuns (operação tributada com PIS isento)
- Field names: `item_N_cst_pis`, `item_N_cst_cofins`

## Schema atualizado

```python
# SF1 — Lote 4 adicionou F1_MENNOTA
SF1_COLS = "..., F1_BASEICM, F1_VALICM, F1_FRETE, F1_SEGURO, F1_DESCONT, F1_DESPESA, F1_MENNOTA"

# SD1 — Lote 4 adicionou D1_DESC, D1_CLASFIS, D1_PICM, D1_BASEICM
SD1_COLS = "..., D1_BM, D1_DESC, D1_CLASFIS, D1_PICM, D1_BASEICM"

# XML parser (_parse_nfe_*) ganhou:
xml_header.inf_cpl       # <infAdic><infCpl>
xml_items[N].cst         # <imposto><ICMS>*<CST>
xml_items[N].csosn       # <imposto><ICMS>*<CSOSN>
xml_items[N].p_icms      # <imposto><ICMS>*<pICMS>
xml_items[N].cest        # <prod><CEST>
xml_items[N].cst_pis     # <imposto><PIS>*<CST>
xml_items[N].cst_cofins  # <imposto><COFINS>*<CST>
```

## FRENTE 2 — UI Polish (status)

| Item | Status | Notas |
|---|---|---|
| Admin sem Transmite/TSS/Smartlink | ✅ entregue em Hotfix2 | só NFStock + A1 visíveis |
| Modal multi-select de filiais | ✅ entregue em Hotfix | `<select multiple>` populado de `/api/settings/public` |
| Checkboxes modelos (NF-e, CT-e, NFS-e, MDF-e) | ✅ entregue em Hotfix | já presentes no modal |
| Dark mode .main / modal-content / inputs | ✅ entregue em Hotfix2 | overrides `body.dark-mode .main/.topbar/.form-control` |
| Toggle Sol/Lua no rodapé | ✅ entregue em Hotfix2 | `margin-top:auto` + `order:99` |
| Visão Operador limpa | ✅ entregue em Hotfix2 | `body.role-operator` esconde footer-info + form-text |
| 🆕 Botão "Limpar Resultados" | ✅ Sprint 10 | reseta filtros + tabelas sem F5 |

### 🆕 Botão "Limpar Resultados" no Auditor

```js
$("btnClearResults").addEventListener("click", () => {
  // 1) Reseta inputs de filtro
  ["fFrom","fTo","fBranch"].forEach(id => $(id).value = "");
  $("fSev").value = ""; $("fNcmOnly").checked = false; $("fIncludeAcked").checked = false;
  // 2) Limpa tabelas das duas abas (Anomalias / Pendentes)
  $("rows").innerHTML = `<tr><td colspan="7">...Clique em Filtrar...</td></tr>`;
  $("rowsPending").innerHTML = `<tr><td colspan="5">—</td></tr>`;
  // 3) Zera contadores das abas
  $("countReal").textContent = "0"; $("countPending").textContent = "0";
  // 4) Toast de confirmação
  toast("Resultados limpos.", "info");
});
```

Posicionado entre "Testar fonte XML" e "+ Nova auditoria" no toolbar.
Não requer chamada à API — operação 100% local.

## Smoke test

```
=== Parse XML Sprint 10 ===
inf_cpl: "Pedido de Compra 12345 / Contrato CT-2026-001 / Centro de Custo 100"
items[0].cst: "000"
items[0].cest: "0100100"
items[0].p_icms: "18.0000"
items[0].cst_pis: "01"
items[0].descricao: "PARAFUSO INOX 6X20MM"

=== Cenário OK (Protheus == XML) ===
Anomalias Sprint 9+10: 0 ✓

=== Cenário BAD (todas as 7 regras Sprint 10 acionam) ===
[OK] item_1_descricao        severity=warn       (regra 16)
[OK] item_1_cst              severity=critical   (regra 17)
[OK] item_1_icms_aliquota    severity=critical   (regra 18a)
[OK] item_1_icms_valor       severity=critical   (regra 18b)
[OK] item_1_icms_base        severity=critical   (regra 18c)
[OK] info_complementares     severity=warn       (regra 19)
[OK] item_1_cest             severity=warn       (bonus)

criticals: 4/4 (regras 17, 18a, 18b, 18c)
warns:    3/3 (regras 16, 19, CEST)
```

## Estatísticas finais do Rule Engine

**Total de regras implementadas:** 19 + 2 bonus + 8 legacy = **29 cruzamentos**

| Categoria | Regras | Severidade típica |
|---|---|---|
| Lote 1 — Cabeçalho (Sprint 9) | 5 | crítica × 3, warn × 2 |
| Lote 2 — Totais/Impostos (Sprint 9) | 5 | crítica × 2, warn × 3 |
| Lote 3 — Itens base (Sprint 9) | 5 × N | crítica × 2, warn × 3 |
| **Lote 4 — Lote final (Sprint 10)** | **4 + bonus** | crítica × 3, warn × 3, info × 2 |
| Legacy (Sprint 4.B/6/8.2) | 8 | misto |

## Como adicionar a regra 20+

1. Definir `_check_<nome>(self)` ou `_check_<nome>(self, item, xi)`
2. Reusar `self._norm_decimal`, `self._norm_digits`, `self._normalize_text_for_match`, `self._text_similar`
3. Chamar `self._add(field, protheus, xml, severity, note)` quando divergente
4. Adicionar a chamada em `run()` no lote apropriado (4 ou novo lote 5)

## Próximos (sugestões pós Sprint 10)

- **Regra 20**: CST IPI por item — se NCM exige IPI mas item tem CST 99 (sem incidência)
- **Regra 21**: Cross-check soma dos `<vICMS>` itens contra `<vICMS>` em `<ICMSTot>`
- **Regra 22**: Validação de CFOP (operação interestadual vs interna) baseado em UF emitente
- **Testes unitários** do `FiscalRuleEngine` (uma classe por regra agora é trivial — cada `_check_*` é isolado)
- **Painel admin "Catálogo de Regras"** com toggle on/off por regra (setting `FISCAL_RULE_<nome>_ENABLED`)

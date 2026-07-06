# Auditor Fiscal — Cruzamentos (spec do cliente × motor) — 2026-06-13

Mapa autoritativo informado pela Fertimaxi e a cobertura atual no
`backend/fiscal/rule_engine.py` + `internal_audit.py`. **CTe é lançado como
Tipo da Nota = Complemento e Normal** (o motor compara SF1×SDS independente do
tipo, desde que o documento exista nas duas pontas).

## Cabeçalho (ERP SF1 × XML SDS) — 12 cruzamentos

| Campo | ERP × XML | Motor | Status |
|---|---|---|---|
| Número | F1_DOC × DS_DOC | `numero_nota` | ✅ |
| Série | F1_SERIE × DS_SERIE **(ou DS_SDOC)** | `serie` (fallback DS_SDOC add. v2.13.1) | ✅ |
| Data Emissão | F1_EMISSAO × DS_EMISSA | `data_emissao` | ✅ |
| Data Digitação/Import. | F1_DTDIGIT × DS_DATAIMP | `data_digitacao` (v2.16.0; destacada no header) | ✅ |
| Espécie | F1_ESPECIE × DS_ESPECI | `especie` (v2.16.0) | ✅ |
| CNPJ Fornecedor | F1_FORNECE+F1_LOJA × DS_CNPJ | via SA2.A2_CGC × DS_CNPJ | ✅ |
| Valor Produto | F1_VALMERC × DS_VALMERC | `valor_mercadoria` | ✅ |
| ~~Base ICMS~~ | ~~F1_BASEICM × DS_BASEICM~~ | **removido do cabeçalho (v2.14.3)** — ICMS auditado por item | ➖ |
| ~~Valor ICMS~~ | ~~F1_VALICM × DS_VALICM~~ | **removido do cabeçalho (v2.14.3)** — ICMS auditado por item | ➖ |
| Frete | F1_FRETE × DS_FRETE | `frete` | ✅ |
| Seguro | F1_SEGURO × DS_SEGURO | `seguro` | ✅ |
| Desconto | F1_DESCONT × DS_DESCONT | `desconto` | ✅ |
| Outras Despesas | F1_DESPESA × DS_DESPESA | `despesas` | ✅ |
| Valor Total | F1_VALBRUT × DS_TOTAL | `valor_total` | ✅ |

## Itens (ERP SD1 × XML SDT) — por item

| Campo | ERP × XML | Motor | Status |
|---|---|---|---|
| Valor Unitário | D1_VUNIT × DT_VUNIT | `item_N_valor_unit` | ✅ |
| Quantidade | D1_QUANT × DT_QUANT | `item_N_quantidade` | ✅ |
| Descrição | **D1_FSDPROD** × DT_DESCFOR | `item_N_descricao` (D1_FSDPROD ERP, fallback D1_DESC; DT_DESC ← DT_DESCFOR; similaridade ≥75%) | ✅ (v2.14.1) |
| CST | **D1_CLASFIS (SD1) × DT_CLASFIS (XML)** | `item_N_cst` — considera APENAS o SD1 no lado ERP (SFT desconsiderado a pedido, v2.16.1) | ✅ |
| CFOP | D1_CF × DT_CODCFOP | `item_N_cfop` (DT_CFOP ← DT_CODCFOP) | ✅ |
| Alíquota ICMS | D1_PICM × DT_XMLICM | `item_N_aliquota_icms` (DT_PICM ← DT_XMLICM) | ✅ |

Extras já cobertos pelo motor (além da spec): Valor Total do item, Base ICMS do
item, Valor ICMS do item, e detecção de item ausente em SD1↔SDT.

## Notas / pendência opcional

- **DT_*** são nomes canônicos; o `internal_audit._detect_sdt_columns` mapeia em
  runtime as variantes do cliente:
  - Base ICMS: **DT_XBASICM** → DT_BASEICM
  - Valor ICMS: **DT_XMLICM** → DT_VALICM
  - Alíquota ICMS: **DT_XALQICM** → DT_PICM (v2.14.2; fallback DT_ALIQICM/DT_PICM)
  - CFOP: DT_CODCFOP → DT_CFOP · Descrição: DT_DESCFOR → DT_DESC
- **DS_SDOC** (série do CTe) é incluído na carga **só se a coluna existir**
  (`_table_has_column`), para não quebrar releases sem ela.
- **CST (v2.16.1):** considera **apenas o SD1** (`D1_CLASFIS × DT_CLASFIS`). O
  cruzamento via SFT (FT_CLASFIS) chegou a existir (v2.14.0) mas foi **removido a
  pedido** — a carga do SFT saiu do `internal_audit` (sem consulta extra ao Protheus).

## Filtrar / selecionar pontos (v2.14.0)
- **Na tela:** o relatório/detalhe do documento tem barra de **busca + filtro por
  status** (Todos/Divergentes/Match/Sem dado) — a analista isola os pontos a cruzar.
- **No Excel:** o botão **"Exportar relatório"** gera planilha filtrável (formato
  longo, 1 linha por ponto cruzado, datas BR `DD/MM/AAAA`, autofiltro). Com o toggle
  "Exibir apenas divergências" **desligado**, traz todos os documentos.

# Sprint 5 — Motor de Cruzamento (JOINs) + UX de Colunas (2026-05-16)

## 📐 Arquitetura

```
   Frontend (protheus.js)                    Backend
   ───────────────────────                   ───────────────
   1. Base: alias + branch                   POST /api/protheus/query
   2. + Adicionar Relacionamento  ────►      payload com `joins: [...]`
      tabela B + tipo + ON                       │
   3. Caixa de colunas mescla 1+N                ▼
      tabelas com filtro de busca           JoinQueryBuilder
                                                 │ SQL parametrizado
                                                 ▼
                                            t1 / t2 / t3... aliases SQL
                                            ORDER BY t1.R_E_C_N_O_
                                            D_E_L_E_T_=' ' em CADA tabela
                                                 │
                                                 ▼
                                            rows com chaves
                                            SC5__C5_NUM, SA1__A1_NOME...
```

## 🆕 Backend

### Schemas — [`backend/schemas.py`](../backend/schemas.py)

```python
class JoinOn(BaseModel):
    left_alias: str           # tabela já no FROM/JOIN (base ou anterior)
    left_column: str
    right_column: str         # da tabela sendo juntada (alias da JoinClause)

class JoinClause(BaseModel):
    alias: str
    branch: str
    join_type: Literal["INNER", "LEFT"] = "INNER"
    on: List[JoinOn]          # min 1

class ProtheusQueryRequest(BaseModel):
    # campos antigos +
    joins: Optional[List[JoinClause]] = None
    # `columns` aceita "FOO" (auto-prefixa base) ou "ALIAS.FOO" (qualificada)
```

### Motor — [`backend/query_engine.py`](../backend/query_engine.py)

Novo módulo `JoinQueryBuilder` ~300 linhas. Pontos-chave:

- **Aliases SQL** `t1, t2, t3...` para cada tabela (mantém SQL legível e curto).
- **`D_E_L_E_T_ = ' '` em CADA tabela** (regra Protheus de exclusão lógica).
- **Sanitização**: todos identificadores passam por `_safe_ident` (regex estrita).
- **Bind params** em valores (`:f_0`, `:_offset`, `:_limit`).
- **Output qualificado**: `t1.C5_NUM AS [SC5__C5_NUM]` evita colisão quando 2 tabelas têm coluna com mesmo nome.
- **Paginação estável**: `ORDER BY t1.R_E_C_N_O_ OFFSET ... FETCH NEXT ...`.
- **2 queries**: data + count (mesmo WHERE).
- **Rejeição em SELECT \***: com JOIN, força usuário a escolher colunas (evita payloads explosivos).

### Rota refatorada — [`backend/routers/protheus_routes.py`](../backend/routers/protheus_routes.py)

`POST /api/protheus/query` ramifica:
- Se `payload.joins` → `run_join_query()` (motor novo).
- Caso contrário → `protheus_api.query_table()` (single-table, compat total).

Whitelist por perfil é aplicada à base **E a cada tabela do JOIN**.

### Exemplo gerado

Payload:
```json
{
  "alias": "SC5", "branch": "01",
  "joins": [
    {"alias":"SA1","branch":"01","join_type":"INNER",
     "on":[{"left_alias":"SC5","left_column":"C5_CLIENTE","right_column":"A1_COD"}]},
    {"alias":"SA3","branch":"01","join_type":"LEFT",
     "on":[{"left_alias":"SC5","left_column":"C5_VEND1","right_column":"A3_COD"}]}
  ],
  "columns": ["SC5.C5_NUM","SC5.C5_EMISSAO","SA1.A1_NOME","SA3.A3_NOME"],
  "rules": [{"field":"SC5.C5_EMISSAO","op":"gte","value":"20260101"}]
}
```

SQL gerado (validado no smoke test):
```sql
SELECT t1.C5_NUM        AS [SC5__C5_NUM],
       t1.C5_EMISSAO    AS [SC5__C5_EMISSAO],
       t2.A1_NOME       AS [SA1__A1_NOME],
       t3.A3_NOME       AS [SA3__A3_NOME]
FROM   SC5010 WITH (NOLOCK) AS t1
       INNER JOIN SA1010 WITH (NOLOCK) AS t2 ON t1.C5_CLIENTE = t2.A1_COD
       LEFT  JOIN SA3010 WITH (NOLOCK) AS t3 ON t1.C5_VEND1   = t3.A3_COD
WHERE  t1.D_E_L_E_T_ = ' '
   AND t2.D_E_L_E_T_ = ' '
   AND t3.D_E_L_E_T_ = ' '
   AND t1.C5_EMISSAO >= :f_0
ORDER BY t1.R_E_C_N_O_
OFFSET :_offset ROWS FETCH NEXT :_limit ROWS ONLY
```

Cada linha retorna `{"SC5__C5_NUM":"00001", "SA1__A1_NOME":"FERTILIZANTES X LTDA", ...}`.

## 🆕 Frontend ([`protheus.js`](../frontend/js/protheus.js))

### 1. Bloco "+ Adicionar Relacionamento"

Card colorido (verde Fertimaxi à esquerda) com:
- **Tipo JOIN**: dropdown INNER/LEFT.
- **Tabela B**: dropdown que exclui aliases já usados.
- **Filial B**: usa as filiais fixas (`/api/settings/public`).
- **Condição ON** visual: `[Tabela A] . [Coluna A] = [Tabela B] . [Coluna B]`.
- Múltiplas condições com **+ AND** mais uma.
- Botão remover.

A "Tabela A" da condição ON pode ser **a base OU qualquer JOIN anterior** — permite encadear: `SC5 → SC6 → SB1` em cascata.

### 2. Mescla de colunas (passo 2)

Quando há JOIN, a caixa "Colunas a exibir" mostra **grupos visuais** por tabela:
```
┌─ SC5  Pedidos de Venda  (24)
│   ☐ C5_NUM — Numero do pedido
│   ☐ C5_CLIENTE — ...
├─ SA1  Clientes  (62)
│   ☐ A1_NOME — Razao social
│   ☐ A1_END — Endereco
└─ SA3  Vendedores  (15)
    ☐ A3_NOME — Nome do vendedor
```

Checkboxes carregam `value="SC5.C5_NUM"` (qualificadas — backend usa direto).

### 3. Filtro de busca de colunas (crítico para UX)

```html
<input id="column-search" placeholder="🔎 Pesquisar coluna (físico C5_NUM ou descrição)…">
```

Evento `input` filtra em tempo real ocultando `display:none` nos itens cujo
`data-search` (`<alias>.<col> <desc>` em lowercase) **não contém o termo**.
Contador "X/Y colunas" no canto direito.

**Esc** limpa o campo.

Grupos sem item visível são auto-ocultados.

### 4. Filtros (passo 3) também mesclam

O dropdown "campo" dos filtros agora lista colunas **qualificadas de todas as tabelas**
(`SC5.C5_EMISSAO`, `SA1.A1_NOME`, etc).

## 🎨 CSS

[`style.css`](../frontend/css/style.css) ganhou:

- `.join-block`, `.join-card`, `.join-card-header`, `.join-card-body`
- `.join-badge` (círculo numerado verde)
- `.on-row` (flex row para condição ON)
- `.column-search-wrap`, `.column-search`, `.column-search-count`
- `.col-group`, `.col-group-header`, `.col-group-body` (agrupamento por tabela)

## ✅ Smoke test

```
SQL gerado:
  t1 (SC5) + INNER JOIN t2 (SA1) ON C5_CLIENTE = A1_COD
              + LEFT  JOIN t3 (SA3) ON C5_VEND1 = A3_COD
  WHERE 3x D_E_L_E_T_=' ' + filtro
  ORDER BY t1.R_E_C_N_O_ OFFSET ... FETCH NEXT ...
  output: ['SC5__C5_NUM', 'SC5__C5_EMISSAO', 'SA1__A1_NOME', 'SA3__A3_NOME']
```

## 🧪 Como testar

1. Login admin → **Consultas Protheus**.
2. Tabela base: **SC5** + filial 01.
3. Caixa "Colunas" mostra as 24 colunas de SC5 — digite "cliente" no filtro → vê só `C5_CLIENTE` e descritivos com "cliente".
4. Botão **"+ Adicionar Relacionamento"** → card aparece.
   - Tipo: `INNER`. Tabela B: `SA1 — Clientes`. Filial: `01`.
   - ON: `SC5 . C5_CLIENTE = SA1 . A1_COD`.
5. Adicione segundo JOIN: `LEFT` SA3, ON `SC5 . C5_VEND1 = SA3 . A3_COD`.
6. Caixa de colunas agora tem 3 grupos. Marque algumas. Digite `nome` no filtro → vê só `A1_NOME`, `A3_NOME` (e qualquer coluna com "nome" na descrição).
7. **Consultar** → grid retorna com `SC5__C5_NUM`, `SC5__C5_EMISSAO`, `SA1__A1_NOME`, `SA3__A3_NOME`.
8. **Exportar XLSX** funciona normal — o worker do Celery aceita o mesmo payload (já está estruturado para `joins[]`, mas o `report_task.py` ainda usa só single-table; próximo passo migrar também).

## 🛡️ Limites e segurança

- Máx 50.000 linhas por página (mesmo limite do `query_table`).
- `_safe_ident` valida cada identificador (alias, coluna) contra `[A-Z][A-Z0-9_]{0,59}`.
- Apenas `INNER` e `LEFT` aceitos (CROSS/RIGHT explicitamente rejeitados).
- Operadores de filtro mantidos do motor single-table (`eq, ne, gt, gte, lt, lte, like, in, between`).
- Whitelist de tabelas por perfil aplicada à base **E a cada JOIN**.
- Em modo JOIN, `SELECT *` é proibido (força escolha consciente de colunas).

## 📦 Backup

`backup/v1.5.0-pre-joins/snapshot.tar.gz` (174 KB) — rollback pronto.

## ⏭️ Próximos passos

1. **Worker do Celery aceitar JOIN no `report_task.py`** — atualmente o batch streaming (xlsx) é só single-table. Migrar para usar `run_join_query()`.
2. **Schedules com JOIN** — mesmo motor, só atualizar UI do `schedules.js`.
3. **Validação de coluna do ON exigir tipo compatível** — hoje aceita qualquer coluna; podemos avisar quando tipos divergem (string × numérico).
4. **Sugestão automática de JOINs** — quando o usuário escolhe SC5 e adiciona SA1, sugerir `C5_CLIENTE = A1_COD` automaticamente (mapeamento estático Protheus).
5. **Limite de tabelas por consulta** — atualmente sem limite explícito; talvez bom limitar a 5 JOINs para evitar abuso.

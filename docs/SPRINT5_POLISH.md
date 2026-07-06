# Sprint 5 — Refinamento (2026-05-16)

Polimento para deixar a feature de JOINs pronta para os testes de segunda:
**limite anti-abuso + worker assíncrono + UI de agendamentos + redesign de e-mails**.

---

## 1️⃣ Limite de 5 JOINs (proteção do banco)

### Backend
[`backend/query_engine.py`](../backend/query_engine.py) ganhou constante
`DEFAULT_MAX_JOINS = 5` e validação no `JoinQueryBuilder._validate`:

```python
max_joins = settings_store.get_setting("QUERY_MAX_JOINS") or DEFAULT_MAX_JOINS
if len(self.joins) > max_joins:
    raise ProtheusError(
        f"Limite de {max_joins} JOINs por consulta excedido (recebido: N). "
        f"Reduza o cruzamento ou peca ao admin para aumentar `QUERY_MAX_JOINS`."
    )
```

Configurável em runtime via `AppSetting('QUERY_MAX_JOINS')` (1..20).

### Frontend
[`protheus.js`](../frontend/js/protheus.js) e [`schedules.js`](../frontend/js/schedules.js):
- Constante `MAX_JOINS = 5` (sincronizada).
- Botão `+ Adicionar Relacionamento` mostra contador `(N/5)` no label.
- 6º clique exibe toast amarelo:
  > "Limite de 5 relacionamentos por consulta atingido — proteção do banco. Reduza ou peça ao admin."
- Botão fica `disabled` automaticamente quando bate o teto.

Smoke test confirmou: tentativa de 6 JOINs no builder gera erro com mensagem clara.

---

## 2️⃣ Worker Celery aceita JOINs

[`backend/queue/tasks/report_task.py`](../backend/queue/tasks/report_task.py) reescrito
com **2 helpers** que constroem queries:

| Helper | Cenário | Saída |
|---|---|---|
| `_build_single_query(payload)` | Compat single-table | `(data_sql, count_sql, params, name_hint)` |
| `_build_join_query(payload)` | Sprint 5 — JOIN | usa `JoinQueryBuilder`, remove OFFSET (chunksize cuida) |

Fluxo unificado:
1. Detecta `payload.joins` → escolhe builder.
2. Conta linhas (COUNT_BIG) para `progress_pct`.
3. `pd.read_sql(text(sql), chunksize=10000)` itera.
4. `write_xlsx_stream` ou `write_csv_stream` grava no `.tmp` + rename atômico.

**Headers da planilha** refletem corretamente:
- Single: `C5_NUM`, `C5_CLIENTE`, ...
- JOIN  : `SC5__C5_NUM`, `SA1__A1_NOME`, ... (alias da tabela com `__`)

### `/api/reports/jobs` empacota JOINs

[`jobs_routes.py`](../backend/routers/jobs_routes.py): quando `payload.joins`
está presente:
- Valida whitelist de cada tabela do JOIN contra os perfis do user.
- Envia para o `job.payload_json`:
  ```json
  {
    "alias": "SC5", "branch": "01",
    "joins": [
      {"alias":"SA1","branch":"01","join_type":"INNER",
       "on":[{"left_alias":"SC5","left_column":"C5_CLIENTE","right_column":"A1_COD"}]}
    ],
    "columns": ["SC5.C5_NUM","SA1.A1_NOME"],
    "file_format": "xlsx"
  }
  ```

---

## 3️⃣ Schedules UI — bloco JOIN + busca de colunas

[`schedules.js`](../frontend/js/schedules.js) ganhou:

- **Bloco "+ Adicionar Relacionamento"** abaixo de tabela/filial — mesmo padrão de UX do `protheus.js`:
  - Tipo INNER/LEFT, dropdown Tabela B (exclui usadas), filial B.
  - Condição ON visual com cascata: "Tabela A" pode ser a base ou JOIN anterior.
  - `+ AND mais uma condição` para múltiplas regras de ON.
  - Limite 5 sincronizado.
- **Filtro de busca de colunas** (`#sColSearch`) idêntico ao do builder principal.
- **Mescla por tabela** com grupos visuais + contador "X/Y colunas".
- Submit empacota `joins[]` no payload.
- Validações: 
  - Se tem JOIN sem colunas selecionadas → toast.
  - ON incompleto → toast por relacionamento/condição.

Agendamentos recorrentes agora podem cruzar tabelas — uma planilha "Pedidos + Cliente + Vendedor" sai todo dia útil às 8h por e-mail.

---

## 4️⃣ Redesign de e-mails HTML (identidade Fertimaxi)

### Estrutura nova

```
backend/email_templates/
├── _base.html             ← wrapper compartilhado (header verde + footer)
├── password_reset.html    ← Recuperação de senha
├── welcome.html           ← Boas-vindas a novo usuário
└── report.html            ← Entrega de relatório agendado
```

**Decisões de design**:
- **Table-based layout** (não flexbox/grid) — Outlook corporate não renderiza CSS moderno.
- **Cores inline** (clientes ignoram `:root` e `<style>`).
- **Header com gradient verde→azul** (paleta Fertimaxi `#0F4C8C → #2E8B3D`).
- **Pre-header invisível** (aparece na lista de e-mails do Outlook/Gmail).
- **Cards de KPI** para o relatório com 3 colunas (Tabela / Linhas / Período).
- **Footer** com nome do app, fuso de Brasília, aviso "mensagem automática".

### Engine de templates

Substituição simples `{{KEY}}` (não Jinja — evita dependência extra para 3 templates).
[`email_service.py`](../backend/email_service.py) tem:

```python
def _render_template(name, ctx) -> str:
    # carrega arquivo + substitui {{KEY}} por ctx[KEY]

def _render_full(template_name, **ctx) -> str:
    # 1) renderiza o transacional -> body
    # 2) renderiza _base.html com body no placeholder {{BODY}}
```

### 3 funções públicas

| Função | Quando dispara | Onde |
|---|---|---|
| `send_temp_password()` | Esqueci a senha | `auth_routes.forgot_password` |
| `send_welcome()` | **NOVO** — admin cria usuário | `users_routes.create_user` (best-effort, audit log) |
| `send_report()` | **NOVO** — agendamento dispara | `scheduler._execute_schedule` (substitui body texto) |

### Variáveis de ambiente novas

| Setting | Uso | Default |
|---|---|---|
| `APP_PUBLIC_URL` | Base URL para links absolutos nos e-mails (logo + login) | `""` (usa path relativo) |
| `QUERY_MAX_JOINS` | Override do limite de JOINs | 5 |

---

## ✅ Smoke test (todos passam)

```
DEFAULT_MAX_JOINS: 5
Bloqueio 6 JOINs OK: "Limite de 5 JOINs por consulta excedido..."
Worker JOIN SQL gerado: True (sem OFFSET, pandas chunksize cuida)
Worker hint: SC5_join1
Template _base.html: True
Template password_reset.html: True
Template welcome.html: True
Template report.html: True
Render password_reset OK: True (size: 4154 bytes)
total rotas: 88
```

---

## 📦 Backup

`backup/v1.5.1-pre-polish/snapshot.tar.gz` (186 KB) — rollback pronto.

---

## 🧪 Como testar segunda-feira (com VPN)

1. **Limite de JOINs**: tente adicionar 6 → toast warning, botão desabilita no 5º.
2. **Agendamento com JOIN**: novo agendamento → SC5 + SA1 (INNER) + SA3 (LEFT) → escolha colunas qualificadas → "Rodar agora" → e-mail chega com XLSX e headers `SC5__C5_NUM`, `SA1__A1_NOME`.
3. **E-mail "boas-vindas"**: crie usuário novo na tela de admin → o e-mail dele recebe template HTML com credenciais formatadas em card verde.
4. **E-mail "esqueci a senha"**: peça reset → caixa do destinatário recebe template com senha temporária em destaque verde claro.
5. **E-mail "relatório"**: agendamento existente → executa → e-mail chega com 3 cards de KPI (Tabela / Linhas / Período) + anexo.

---

## ⏭️ Próximas sugestões (pós-segunda)

- **Sugestão automática de ON**: mapa estático Protheus (`SC5.C5_CLIENTE ↔ SA1.A1_COD`, `SC5.C5_VEND1 ↔ SA3.A3_COD`...) para preencher automaticamente ao adicionar SA1/SA3 sobre SC5.
- **Dicionário SX2/SX3 reais** para enriquecer as descrições das colunas no Builder (hoje o `c.description` vem vazio).
- **Preview de SQL** no Builder antes de consultar (collapsible mostrando o SQL final + tooltips de aliases).
- **Histórico de queries** salvas por usuário (favorit-las para reuso).

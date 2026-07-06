# Fase 4 — Plano técnico (itens 1 e 4)

Status atual da Fase 4: **itens 2 e 3 entregues** (filiais fixas + versão/TZ na UI).
**Itens 1 e 4 aqui descritos** aguardam aprovação antes de implementar.

---

## Item 1 — TransmiteSource + cruzamento das 8 tabelas Protheus

### 1.1. Pivô estratégico

| Antes (Fase 3) | Agora (Fase 4) |
|---|---|
| `A1CertSource` lê `.pfx`, faz SOAP direto na SEFAZ | **`TransmiteSource`** consome REST API do TOTVS Transmite |
| Empresa gerencia validade A1, endpoints UF, etc. | Empresa só configura URL + user + senha; TOTVS resolve cert/UF |
| `TSSSource` (servidor TSS on-premise) | **continua disponível** para clientes que não usam Transmite Cloud |

**Decisão:** `A1CertSource` será **deprecado** (mantido como stub `NotImplementedError` para não quebrar settings antigos). `TransmiteSource` vira a fonte default.

### 1.2. API TOTVS Transmite — modelo de comunicação

O TOTVS Transmite (`transmite.totvs.app`) expõe REST que segue o padrão de
plataformas TOTVS:

1. **Autenticação** — login retorna token JWT/bearer (TTL ~8h).
   - `POST /api/auth/login` body `{ "user": "...", "password": "..." }` → `{ "token": "...", "expires_in": 28800 }`
2. **Consulta de NFe por chave** — usando o token:
   - `GET /api/nfe/{chave}/xml` → retorna XML cru (ou JSON com `xml_base64`)
   - ou `GET /api/documentos?chave=...` → JSON `{ documents: [{ xml: "..." }] }`
3. **Renovação automática** — token expirando → re-login transparente.

Como a API exata do Transmite varia por release/contrato, implementaremos
com a mesma estratégia já usada em `TSSSource`/`NfstockSource`:
**múltiplas rotas candidatas** + auto-detect na primeira chamada bem-sucedida.

### 1.3. Configuração nova (Wizard + Admin)

Settings adicionados ao scope `api`:

| Chave | Tipo | Descrição |
|---|---|---|
| `TRANSMITE_URL` | string | Base URL (ex: `https://transmite.totvs.app`) |
| `TRANSMITE_USER` | string | Usuário Transmite |
| `TRANSMITE_PASSWORD` | secret | Senha Transmite (Fernet) |
| `TRANSMITE_TIMEOUT` | int | Default 30s |
| `TRANSMITE_TOKEN_CACHE_SECONDS` | int | Default 25200 (7h, abaixo do TTL 8h) |

Adicionar no:
- **Wizard** (`setup.html`/`setup.js`) — passo 4 "APIs externas" ganha bloco TOTVS Transmite.
- **Admin** (`admin.js`) — aba Configurações > APIs externas ganha mesmo bloco.
- **Auditor Fiscal Config** — `FISCAL_SOURCE` ganha opção `transmite` (default novo).

### 1.4. Estrutura do `TransmiteSource`

`backend/fiscal/xml_sources/transmite.py`:

```python
class TransmiteSource(XmlSource):
    name = "transmite"
    _token: str | None = None
    _token_exp: datetime | None = None
    _lock: threading.Lock = ...

    def _cfg(self): ...                # le settings_store
    def is_configured(self): ...       # url + user + password
    def _login(self) -> str: ...       # POST /api/auth/login → token, cacheia
    def _ensure_token(self) -> str: ...# valida exp, re-loga se necessario
    def _request(method, path, ...):   # injeta Authorization, retry 401 com re-login
    def get_xml(self, chave) -> bytes: # tenta varias rotas candidatas
```

Erros mapeados:
- credenciais erradas → `XmlSourceError` "Transmite: login falhou" (mapeia ERR-FISCAL-004)
- NFe não encontrada → `XmlNotFound`
- timeout/rede → `XmlSourceError`
- token expirado → re-loga automaticamente (1 retry)

### 1.5. Cruzamento das 8 tabelas — arquitetura

O auditor atual já lê **SF1 + SD1** (Fase 3). Para a Fase 4, expandimos para
**8 tabelas com nivel de validacao maior**.

```
                  ┌──────────────────┐
                  │  TransmiteSource │
                  └────────┬─────────┘
                           │ chave NFe (F1_CHVNFE)
                           ▼
                  ┌──────────────────┐
                  │  XML autorizado  │
                  └────────┬─────────┘
                           │  parse (lxml)
                           ▼
  ┌─────────────────────────────────────────────────────────────┐
  │                  Cruzamento Protheus x XML                  │
  └─────────────────────────────────────────────────────────────┘

  Cabeçalho:
    SF1 — F1_CHVNFE, F1_VALBRUT, F1_SERIE, F1_FORNECE/LOJA, F1_NFEFOR
    └─ compara: chave_acesso, valor_total, série, CNPJ

  Itens (compara cada nItem):
    SD1 — D1_DOC+D1_SERIE+D1_FORNECE+D1_LOJA = SF1
    └─ compara: D1_QUANT, D1_VUNIT, D1_TOTAL, D1_CFOP × xml/det/prod

  Integridade do XML internalizado:
    SDT — Itens lidos do XML pelo Protheus (espelho)
    └─ regra: linhas de SDT devem bater linha-a-linha com xml/det
              (detecta perda de itens na importacao do XML)

  Controle de coleta:
    CKOCOL — Chave deve aparecer na coleta (campo CK0_CHVNFE)
    └─ regra: se ausente, anomalia "XML nao foi coletado oficialmente"

  Centro de custo / rateio:
    SDE — DE_FILIAL + DE_DOC + DE_SERIE + DE_FORNECE
    └─ compara: rateio (DE_VALOR somado = D1_TOTAL do item)

  Financeiro:
    SE2 — E2_PREFIXO + E2_NUM = F1_DOC + F1_SERIE
    └─ regra: SUM(E2_VALOR) = F1_VALBRUT (titulos casam com NF)
    └─ comparar com xml/cobr/dup quando presente

  Livros fiscais:
    SFT — Itens (FT_FILIAL + FT_NFISCAL + FT_SERIE)
    └─ valida CFOP, base ICMS, valor ICMS item-a-item
    SF3 — Cabecalho (F3_FILIAL + F3_NFISCAL + F3_SERIE)
    └─ valida total ICMS/IPI vs xml/total/ICMSTot
```

### 1.6. Novos comparators (`backend/fiscal/comparators.py`)

Funções a adicionar (puras, recebem dict do Protheus + dict do XML, retornam `Optional[Divergence]`):

| Função | Tabela origem | Severidade default |
|---|---|---|
| `compare_xml_internalized(sdt_rows, xml_items)` | SDT | warn (falta item) / critical (item extra) |
| `compare_collection(ckocol_row, chave)` | CKOCOL | warn |
| `compare_cost_center(sde_rows, sd1_row)` | SDE | warn (rateio não bate) |
| `compare_titles_total(se2_rows, sf1_row)` | SE2 | critical (diferença > R$ 0,05) |
| `compare_titles_vs_xml(se2_rows, xml_cobr)` | SE2 × XML | warn |
| `compare_fiscal_items(sft_rows, xml_items)` | SFT | critical (CFOP divergente) / warn (ICMS) |
| `compare_fiscal_header(sf3_row, xml_total)` | SF3 | critical |
| `compare_cfop(sd1_row, xml_item)` | SD1 × XML | critical |

### 1.7. Refactor do `auditor.py`

Atual:
```python
def _iter_protheus_docs(engine, branch, date_from, date_to):
    # le SF1 + SD1
```

Novo:
```python
def _iter_protheus_docs(engine, branch, date_from, date_to):
    # le SF1 + SD1 (como hoje)

def _load_doc_extras(engine, branch, doc, serie, fornece, loja, chvnfe):
    """Carrega SDT, CKOCOL, SDE, SE2, SFT, SF3 do documento.

    Cada um e' opcional — se a tabela nao existir ou nao tiver dados,
    o comparator ignora (nao gera anomalia falsa).
    """
    return {
        "sdt":    _load_sdt(engine, branch, chvnfe),
        "ckocol": _load_ckocol(engine, branch, chvnfe),
        "sde":    _load_sde(engine, branch, doc, serie, fornece, loja),
        "se2":    _load_se2(engine, branch, doc, serie, fornece, loja),
        "sft":    _load_sft(engine, branch, doc, serie, fornece),
        "sf3":    _load_sf3(engine, branch, doc, serie, fornece),
    }

def _compare_doc(protheus_doc, xml_header, xml_items, extras):
    # Aplica os comparators existentes (Fase 3) +
    # compare_xml_internalized, compare_collection, compare_cost_center,
    # compare_titles_total, compare_fiscal_items, compare_fiscal_header.
```

### 1.8. Performance e robustez

- **Batch lookup**: para um período de N notas, fazer **1 query por tabela**
  agrupando IDs em vez de N queries por nota (evita N+1). Ex.:
  `SELECT * FROM SE2010 WHERE D_E_L_E_T_ = ' ' AND E2_PREFIXO + E2_NUM IN (...)`.
- **Cache por execução**: dicionários `{chave_nf: registros}` em memória do worker.
- **Tolerâncias configuráveis**: `AppSetting('FISCAL_TOLERANCE_VALOR_RS', 0.05)`, etc.
- **Limite de erros**: já existe (`FISCAL_MAX_CONSECUTIVE_ERRORS`); aplica também ao Transmite.

### 1.9. Plano de implementação (estimativa)

| Sprint | Entrega | Tempo |
|---|---|---|
| 4.1 | `TransmiteSource` (login + cache token + 3 rotas candidatas) + settings | 1 dia |
| 4.2 | Wizard/Admin com bloco Transmite + opção `FISCAL_SOURCE=transmite` | 0.5 dia |
| 4.3 | Batch loaders `_load_sdt/ckocol/sde/se2/sft/sf3` | 1 dia |
| 4.4 | Novos `compare_*` em `comparators.py` + testes unitários | 1.5 dia |
| 4.5 | Refactor `auditor._compare_doc` para usar `extras` | 0.5 dia |
| 4.6 | Template HTML do e-mail enriquecido (8 seções por anomalia) | 0.5 dia |
| 4.7 | Smoke test end-to-end com NFe real (homologação Transmite) | 1 dia |
| **Total** | | **~6 dias úteis** |

---

## Item 4 — Perfis/Módulos + categorização de tabelas

### 4.1. Modelo de dados

3 novas tabelas:

```python
class Profile(Base):
    """Perfil/Modulo: Logistica, Contabil, Controladoria, etc."""
    __tablename__ = "profiles"
    id          = Column(Integer, PK)
    code        = Column(String(20), unique=True)  # ex: "LOGISTICA"
    label       = Column(String(60))               # ex: "Logística"
    description = Column(Text, nullable=True)
    created_at  = Column(DateTime, default=utcnow)


class TableProfile(Base):
    """Associacao N:N — uma tabela pode pertencer a multiplos perfis."""
    __tablename__ = "table_profiles"
    __table_args__ = (UniqueConstraint("alias", "profile_id"),)
    id         = Column(Integer, PK)
    alias      = Column(String(4), index=True)     # ex: "SE1"
    profile_id = Column(Integer, FK("profiles.id"))


class UserProfile(Base):
    """Vincula usuario a um ou mais perfis."""
    __tablename__ = "user_profiles"
    __table_args__ = (UniqueConstraint("user_id", "profile_id"),)
    id         = Column(Integer, PK)
    user_id    = Column(Integer, FK("users.id"))
    profile_id = Column(Integer, FK("profiles.id"))
```

### 4.2. Seed inicial

Migração no `lifespan` cria os 8 perfis canônicos pedidos:

```python
SEED_PROFILES = [
    ("LOGISTICA",     "Logística"),
    ("CONTABIL",      "Contábil"),
    ("CONTROLADORIA", "Controladoria"),
    ("FINANCEIRO",    "Financeiro"),
    ("PCP",           "PCP"),
    ("ESTOQUE",       "Estoque"),
    ("ADMINISTRATIVO","Administrativo"),
    ("COMERCIAL",     "Comercial"),
]
```

E uma associação default sugerida (admin pode editar depois):

| Alias | Perfis sugeridos |
|---|---|
| SA1, SA3, SC5, SC6 | COMERCIAL |
| SA2, SE2, FK5, FK6 | FINANCEIRO |
| SE1, SE3, SE4, SE5 | FINANCEIRO |
| SF1, SD1, SF3, SFT, SDT, CKOCOL | CONTABIL, CONTROLADORIA |
| SA4, DA4, Z00, ZA4, ZA6, ZAI, ZC4, ZC8 | LOGISTICA |
| SB1, SB5, SB6, SB7, SB8, SBM, SD3 | ESTOQUE |
| SC2, SC3, SC7, SC8, SC9, SCB, SCH, SCP | PCP |
| SCR, SCS, SCV, SAK, SAL | ADMINISTRATIVO |

### 4.3. Endpoints novos

| Verbo | Path | Quem | Função |
|---|---|---|---|
| GET | `/api/profiles` | admin | Lista perfis |
| POST | `/api/profiles` | admin | Cria perfil |
| PUT | `/api/profiles/{id}` | admin | Renomeia/descrição |
| DELETE | `/api/profiles/{id}` | admin | Remove (cascade) |
| GET | `/api/profiles/{id}/tables` | admin | Tabelas associadas |
| POST | `/api/profiles/{id}/tables` | admin | Associa tabela |
| DELETE | `/api/profiles/{id}/tables/{alias}` | admin | Remove associação |
| GET | `/api/users/{id}/profiles` | admin | Perfis do usuário |
| PUT | `/api/users/{id}/profiles` | admin | Substitui perfis |
| GET | `/api/protheus/aliases` (alterado) | view | Agora filtra pelos perfis do usuário corrente |

### 4.4. Frontend

- **Nova página** `/static/pages/profiles.html` (admin) — CRUD de perfis + matriz tabela↔perfil.
- **users.html** — substituir o input "Tabelas Protheus permitidas (CSV)" por seletor de **perfis** (multi-select). A whitelist por tabela continua existindo para casos pontuais, mas o uso normal será via perfis.
- **protheus.js Builder** — dropdown de "Módulo" no topo filtra os aliases disponíveis.
- **Sidebar** — admin tem novo item "Perfis & Módulos".

### 4.5. Backwards compat com a Fase 3

- `UserTablePermission` (whitelist atual por alias) **continua existindo**.
- Lógica do gate: usuário acessa um alias se `(alias na whitelist direta)` OU `(alias em algum dos seus perfis)`.
- Admin pode migrar usuários antigos para perfis via botão "Migrar permissões para perfis".

### 4.6. Plano de implementação

| Sprint | Entrega | Tempo |
|---|---|---|
| 4.A | Models + seed dos 8 perfis + matriz default | 0.5 dia |
| 4.B | Endpoints REST `/api/profiles/*` + `/api/users/{id}/profiles` | 1 dia |
| 4.C | Página `profiles.html` (CRUD + matriz) | 1 dia |
| 4.D | Refactor `users.js` e `protheus.js` para usar perfis | 0.5 dia |
| 4.E | Gate de permissão no `protheus_routes` (incluir perfis no `assert_table_allowed`) | 0.5 dia |
| 4.F | Migrador `UserTablePermission → UserProfile` (botão admin) | 0.5 dia |
| **Total** | | **~4 dias úteis** |

---

## Próxima rodada — ordem sugerida

1. **Item 4 primeiro** (perfis) — destrava a UX do Builder e melhora o controle de acesso.
2. **Item 1 depois** (Transmite) — feature mais isolada, beneficia do controle de acesso já em produção.

Total estimado: **~10 dias úteis** (2 semanas de trabalho focado).

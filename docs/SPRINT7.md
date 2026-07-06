# Sprint 7 — NFStock + Toggle de APIs + Bugfix SX2 (2026-05-18)

Três entregas focadas no Auditor Fiscal e na correção do dicionário Protheus:

| # | Entrega | Status | Estilo |
|---|---|---|---|
| 1 | **NFStockSource** (Alterdata, REST + login Fernet) | ✅ | Backend + UI Admin + endpoint test |
| 2 | **Toggle de Fonte Ativa** (Disabled / A1 / NFStock) | ✅ | UI dropdown + validação + dica contextual |
| 3 | **Bugfix SC2/SC3** + revisão SC* | ✅ | `protheus_aliases.py` |

---

## 1️⃣ NFStockSource — fonte multi-modelo

### Motivação

A SEFAZ Nacional (via Certificado A1) só atende **NF-e modelo 55**. Tentativas
com CT-e (57), MDF-e (58), NFC-e (65) ou NFS-e retornam `cStat=999` (rejeição).
Como a Controladoria audita TODOS os tipos de documento, precisamos de uma
fonte que aceite qualquer modelo: **Alterdata NFStock**.

### Implementação

[`backend/fiscal/xml_sources/nfstock.py`](../backend/fiscal/xml_sources/nfstock.py)
foi reescrito (não é mais o stub Bearer-only da Fase 3):

```python
class NfstockSource(XmlSource):
    name = "nfstock"
    supported_models = None   # None = aceita todos os modelos
```

**Fluxo de autenticação (API MS-Exportação Alterdata — refinado 2026-05-18)**:

Após investigação, a API oficial usa **Personal Access Token estático** —
nada de login/senha/refresh. O admin gera o token no painel Alterdata em
`NFStock > Opções da Conta > Integração` e cola no painel deste sistema.
Toda chamada injeta esse token no header:

```
Authorization: Bearer <TOKEN>           ← padrão
User-Token:   <TOKEN>                   ← alternativo (configurável)
X-Api-Key:    <TOKEN>                   ← alternativo (configurável)
```

Download em `GET {URL}/v1/xml/{chave}` (com fallbacks `/v1/nfe/{chave}/download`,
`/xml/{chave}`, `/nfe/{chave}`). Aceita XML cru OU JSON com `xml_base64`.

Em 401/403 a fonte aborta imediatamente com mensagem clara:
> "Token rejeitado — gere um novo em NFStock > Opções da Conta > Integração."

Sem `_login`, sem `_refresh`, sem cache de token, sem TTL, sem retry de auth.
O ciclo de vida do token é controlado **inteiramente do lado Alterdata** —
revogação/renovação acontecem lá.

**Modo legado**: se `NFSTOCK_TOKEN` está setado e `NFSTOCK_USER` está vazio, usa
o token estático (sem fluxo de login). Mantém compat com instalações antigas.

### Configuração via UI

Painel Admin → APIs externas → **📨 Alterdata NFStock** (bloco em destaque,
borda vermelha — sinaliza "recomendado · multi-modelo"):

| Campo | Setting | Encriptação |
|---|---|---|
| URL **raiz** da API (ex: `https://nfstock.alterdata.com.br`) | `NFSTOCK_URL` | plain |
| **Token de Integração** (Personal Access Token) | `NFSTOCK_TOKEN` | **Fernet (`is_secret=True`)** |
| Estilo do header de auth (`bearer` \| `user-token` \| `x-api-key`) | `NFSTOCK_AUTH_STYLE` | plain |

> ℹ️ A URL deve ser **apenas a raiz** — o cliente monta as rotas
> (`/v1/xml/{chave}`, `/v1/health`) automaticamente. **NÃO** inclua caminhos
> manuais como `/Autenticacao/Login` no campo URL.
>
> O token é gerado pelo admin Alterdata em
> **NFStock → Opções da Conta → Integração** e colado neste painel uma única vez.

Botão **🔎 Testar conexão** chama `POST /api/admin/test/nfstock` que executa
`NfstockSource.ping()`. O ping faz um GET leve em `/v1/health` (com fallback
para `/health` e raiz) usando o token no header. Critério de sucesso: **qualquer
status != 401/403**. Não consome quota de download. Retorna
`{ok, status_code, endpoint, detail}`. O toast verde mostra o status real e o
endpoint que respondeu.

### Confirmação Fernet

[`security_check.py`](../scripts/security_check.py) ganhou
`NFSTOCK_PASSWORD` na lista `MUST_BE_ENCRYPTED` — se o admin acidentalmente
gravar em texto puro, o audit aponta CRITICAL.

---

## 2️⃣ Toggle de Fonte Ativa

### Problema

Antes da Sprint 7, o auditor lia `FISCAL_SOURCE` mas a UI oferecia 4 opções
(`transmite`, `a1`, `tss`, `nfstock`) com comportamento ambíguo — alguns
clientes mantinham credenciais de 3 fontes simultâneas, gerando "concorrência"
e gargalos quando o run_audit instanciava uma e o usuário esperava outra.

### Solução

[`backend/fiscal/xml_sources/base.py`](../backend/fiscal/xml_sources/base.py)
ganhou:

```python
class XmlSourceDisabled(XmlSourceError):
    """admin desativou explicitamente todas as fontes."""

def get_active_source(name=None) -> XmlSource:
    # Default Sprint 7: a1 (não mais 'transmite')
    if name in ("", "disabled", "off", "none"):
        raise XmlSourceDisabled(...)
    # Apenas as 3 oficiais expostas na UI:
    SOURCES = {"a1": A1CertSource, "nfstock": NfstockSource,
               "transmite": ..., "tss": ...}  # legados aceitos mas escondidos
```

### UI Admin → Auditor Fiscal

Dropdown **Fonte de XML Ativa**:

```
⏸ Desativado
🔐 SEFAZ Nacional (Certificado A1) — só NF-e 55
📨 Alterdata NFStock — todos os modelos
```

Cada opção mostra dica contextual abaixo (`fSourceHint`):
- Desativado → "Nenhuma fonte será chamada — execuções vão falhar com mensagem clara."
- A1 → "SÓ NF-e modelo 55. Documentos NFC-e/CT-e/MDF-e/NFS-e serão pulados."
- NFStock → "Aceita TODOS os modelos (55, 65, 57, 58, NFS-e)."

Se o banco tem fonte legada (`transmite`/`tss`), aparece banner: "⚠️ Banco
tem fonte legada — salve para migrar para a opção exibida."

### Instanciação exclusiva no Auditor

`run_audit()` agora:

```python
try:
    source = get_active_source()           # UMA instância, escolhida no Admin
except XmlSourceDisabled as exc:
    raise RuntimeError(str(exc)) from exc

logger.info("Auditor: fonte ativa = %s (supported_models=%s)",
            source.name, source.supported_models or "TODOS")
```

E na hora de cada documento, antes do `get_xml`:

```python
if not source.accepts_model(chave):
    stats["docs_skipped_by_model"] += 1
    logger.info("Doc %s (modelo %s) ignorado: fonte '%s' so aceita %s. "
                "Alterne para NFStock no Admin.", ...)
    continue
```

Ou seja: rodar A1 com chaves CT-e/MDF-e **não erra mais** — pula limpo e
contabiliza em `docs_skipped_by_model`. O cliente recebe orientação no log.

### Validação backend

`POST /api/admin/config/fiscal` valida `FISCAL_SOURCE` contra
`{disabled, a1, nfstock, transmite, tss}`. Qualquer outro valor → 400.

---

## 3️⃣ Bugfix dicionário SX2 — SC2 ≠ SC3

### Problema reportado

No Construtor de Consultas, as tabelas **SC2** e **SC3** apareciam com a
mesma descrição "Ordens de Produção", confundindo o operador (não dava para
saber qual escolher pelo dropdown).

### Diff

[`backend/protheus_aliases.py`](../backend/protheus_aliases.py):

```diff
-    ("SC2", "Ordens de Produção"),
-    ("SC3", "Ordens de Produção"),
+    # Sprint 7 — descrições oficiais TOTVS (corrigido duplicado)
+    ("SC2", "Ordens de Produção"),
+    ("SC3", "Contrato de Parceria"),
     ("SC5", "Pedidos de Venda"),
     ("SC6", "Itens dos Pedidos de Venda"),
-    ("SC7", "Ped. Compra / Aut. Entrega"),
+    ("SC7", "Pedidos de Compra / Aut. Entrega"),
     ("SC8", "Cotações"),
     ("SC9", "Pedidos Liberados"),
-    ("SCB", "Contrato"),
+    ("SCB", "Contratos a Receber"),
     ("SCH", "Rateio Pedido de Compra"),
     ("SCP", "Solicitações ao Armazém"),
     ("SCR", "Documentos com Alçada"),
     ("SCS", "Saldos dos Aprovadores"),
-    ("SCV", "Saldos dos Aprovadores"),
+    ("SCV", "Itens dos Saldos dos Aprovadores"),
-    ("SCY", "Histórico Pedidos de Compras"),
+    ("SCY", "Histórico de Pedidos de Compra"),
```

Revisão completa do bloco SC* — também detectei SCS/SCV duplicadas (mesmo
problema, escondido) e nomes inconsistentes (Ped./Pedido, Contrato/Contratos).

---

## ✅ Smoke test (Sprint 7)

```
SC2: Ordens de Produção        ← diferente
SC3: Contrato de Parceria      ← diferente
SCS: Saldos dos Aprovadores    ← diferente
SCV: Itens dos Saldos dos Aprovadores  ← diferente

NFStock supported_models: None        (aceita todos)
NFStock accepts NF-e 55:    True
NFStock accepts CT-e 57:    True
NFStock accepts MDF-e 58:   True

A1 supported_models: {'55'}
A1 accepts NF-e 55:  True
A1 accepts CT-e 57:  False     ← trava acionada

get_active_source('a1')        → A1CertSource OK
get_active_source('nfstock')   → NfstockSource OK
get_active_source('disabled')  → raises XmlSourceDisabled OK
get_active_source('')          → raises XmlSourceDisabled OK

POST /api/admin/test/nfstock  → registrado (smoke test login flow)

Security audit:  CRITICAL=0 HIGH=0 MEDIUM=0
                 NFSTOCK_PASSWORD listado em MUST_BE_ENCRYPTED ✓
```

---

## 🧪 Como validar na semana

### NFStock (multi-modelo)
1. Admin → APIs → NFStock → preencha URL, CNPJ/CPF + senha → **🔎 Testar login**
   → toast verde "Login NFStock OK".
2. Admin → Auditor Fiscal → Fonte = "📨 Alterdata NFStock — todos os modelos".
3. Rode auditoria num período que tenha CT-e ou NFC-e → planilha de anomalias
   inclui esses documentos.

### Toggle ativo
1. Mude para "⏸ Desativado" → rode auditoria → mensagem clara: "Fonte de XML
   desativada — habilite SEFAZ A1 ou NFStock no painel Admin."
2. Mude para "🔐 A1" → audite um período com chaves NF-e e CT-e → log mostra
   `docs_skipped_by_model: N`. NF-e processadas normalmente. CT-e pulados com
   dica para alternar.
3. Mude para "📨 NFStock" → mesmo período → CT-e processados sem skip.

### Bugfix SX2
1. Construtor → módulo COMERCIAL/PCP → dropdown de tabela mostra:
   - `SC2 — Ordens de Produção` ✓
   - `SC3 — Contrato de Parceria` ✓ (não mais duplicado!)

---

## ⏭️ Pós Sprint 7

- **Auditoria de CT-e/MDF-e** — auditor.py hoje carrega SF1 (NF-e entrada).
  Para NFStock realmente brilhar, precisa também ler `SFK` (CT-e), `SFA`
  (MDF-e), `SF2` (NF-e saída). Próxima sprint.
- **NFStock: paginação por NSU** — pega lote de XMLs dos últimos N dias sem
  saber as chaves antes (útil para "auditar TUDO"). API NFStock suporta.
- **Cache de chave/modelo no DB** — para acelerar trava de modelo evitar
  `chave[20:22]` em loop hot-path.
- **Métrica de uso por fonte** — KPI no Dashboard "NFStock: X reqs/dia,
  latência média Yms" para o admin decidir se trocar para A1 (menor latência
  em NF-e).

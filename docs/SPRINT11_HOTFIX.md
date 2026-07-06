# Sprint 11 — Hotfix Crítico: Refatoração NFStock (API MS-Exportação)

**v2.3.1 · 2026-05-26**

Cliente localizou a documentação oficial da Alterdata e o endpoint antigo
(`/api/v2/Documentos/{chave}/Xml`) estava **incorreto**. A API real (**MS-Exportação NF-Stock**)
exige 3 placeholders na URL + retorna JSON com a chave `xml` (texto ou base64),
não XML cru.

## 1️⃣ Refatoração NFStock

### Default URL atualizada

[`backend/fiscal/xml_sources/nfstock.py`](../backend/fiscal/xml_sources/nfstock.py):

```python
DEFAULT_XML_ENDPOINT = (
    "https://ms-exportacao-nfstock.pack.alterdata.com.br"
    "/api/v1/{codigoCrm}/{cpfCnpj}/documentos/{chave}/chave?Xml=true"
)
DEFAULT_CODIGO_CRM = "444807"   # default Fertimaxi
```

### Novos settings

| Setting | Padrão | Descrição |
|---|---|---|
| `NFSTOCK_CODIGO_CRM` | `444807` | Código CRM fornecido pela Alterdata no contrato |
| `NFSTOCK_CPF_CNPJ` | — | CNPJ da empresa (apenas dígitos; máscara é normalizada automaticamente) |
| `NFSTOCK_XML_ENDPOINT` | URL MS-Exportação acima | Editável; suporta 3 placeholders |

### `_resolve_endpoint(c, chave)` — agora substitui 3 placeholders

```python
if "{codigoCrm}" in full:
    full = full.replace("{codigoCrm}", c["codigo_crm"])
if "{cpfCnpj}" in full:
    full = full.replace("{cpfCnpj}", c["cpf_cnpj"])
return full.replace("{chave}", chave)
```

Lança `XmlSourceError` clara se algum placeholder está no template mas o
valor correspondente não foi configurado:

```
"Endpoint exige {cpfCnpj} mas NFSTOCK_CPF_CNPJ nao esta configurado
(apenas digitos do CNPJ da empresa)."
```

`is_configured()` agora exige `url + token + cpf_cnpj` (CRM tem default).

### Parser de resposta JSON

A nova API retorna `application/json` com a chave `xml` (texto inline OU base64).
Novo helper `_extract_xml_from_json(payload, url)`:

```python
# Procura a chave `xml` em primeiro nivel ou aninhada em `data`/`result`
raw = (
    payload.get("xml")
    or payload.get("xml_base64")              # compat legado
    or payload["data"]["xml"]                 # aninhado
    or payload["result"]["xml"]
)

if raw.lstrip().startswith("<"):
    return raw.encode("utf-8")                # Caso 1: XML texto inline
else:
    return base64.b64decode(raw)              # Caso 2: base64 → bytes
```

Detecção base64 vs texto: olha se o valor começa com `<` (XML real). Caso
contrário, decodifica como base64. Rejeita se decodifica para HTML
(landing page mascarada — `XmlNotFound`).

### 7 cenários testados (smoke end-to-end)

```
[1] JSON {xml: "<NFe>...<NFe>"}                  → texto inline    ✓
[2] JSON {xml: "PD94bWwg..."}                    → base64           ✓
[3] JSON {xml_base64: "..."}                     → compat legado    ✓
[4] JSON {data: {xml: "<NFe>"}}                  → aninhado         ✓
[5] JSON {xml: <base64 de HTML>}                 → XmlNotFound      ✓
[6] JSON {erro: "chave nao localizada"}          → XmlSourceError   ✓
[7] application/xml — XML cru (compat)           → bytes direto     ✓
```

### UI Admin (admin.js)

Bloco **Alterdata NFStock** ganhou 2 campos novos:

```html
<label>Código CRM (Alterdata) — obrigatório</label>
<input id="aNfsCodigoCrm" placeholder="444807">

<label>CNPJ — apenas dígitos (a máscara é removida)</label>
<input id="aNfsCpfCnpj" placeholder="12345678000199" maxlength="18">
```

Placeholder do campo "Endpoint de Download do XML" atualizado para a URL
oficial. Hint do form-text explica os 3 placeholders + que a resposta é JSON.

Save handler valida:
- CNPJ vazio → toast warning
- CNPJ com != 14 dígitos → toast warning (máscara removida automaticamente)
- Endpoint custom sem `{chave}` → toast warning

### Smoke test de URL com substituição

```
config: NFSTOCK_CODIGO_CRM=444807, NFSTOCK_CPF_CNPJ=12345678000199
chave: 33333333333333333333333333333333333333333333

URL resolvida:
  https://ms-exportacao-nfstock.pack.alterdata.com.br
    /api/v1/444807/12345678000199/documentos/33333...3333/chave?Xml=true ✓

CNPJ entrada "12.345.678/0001-99" → normalizado para "12345678000199" ✓
CRM vazio → usa default "444807" ✓
CNPJ vazio → is_configured() retorna False ✓
```

## 2️⃣ UI Polish — verificação dos itens já entregues

Cliente pediu para conferir se os polimentos anteriores ainda valem.
**TODOS OS 5 ITENS já estão em produção desde Sprints anteriores**, intactos:

| Item | Sprint | Status |
|---|---|---|
| Admin sem Transmite/TSS/Smartlink | 8 Hotfix2 | ✅ 0 refs aos IDs antigos no admin.js |
| Modal multi-select de filiais + checkboxes modelos | 8 Hotfix | ✅ `<select multiple>` + `.rDocModel` |
| Botão 🧹 Limpar Resultados (auditor) | 10 + 11 | ✅ presente + bugfix de KPIs/cache |
| Visão Operador (`body.role-operator`) | 8 Hotfix2 | ✅ CSS + layout.js injetam |
| Dark mode `.main`/`.modal-content`/`.table` + toggle no rodapé | 8 Hotfix2 + 11 | ✅ overrides + `margin-top: auto` |

## Smoke test consolidado

```
✓ DEFAULT_XML_ENDPOINT: https://ms-exportacao-nfstock.pack.alterdata.com.br/api/v1/{codigoCrm}/{cpfCnpj}/documentos/{chave}/chave?Xml=true
✓ DEFAULT_CODIGO_CRM: 444807
✓ JSON {xml: inline / base64} parsed corretamente
✓ Validação CNPJ (14 dígitos)
✓ Total rotas: 107
✓ admin.js: 282/282 braces
✓ Security audit: 0 CRITICAL · 0 HIGH · 0 MEDIUM → CONFORMIDADE
```

## Como validar em produção

1. Admin > APIs externas > Alterdata NFStock
2. Preencha:
   - **URL raiz**: `https://ms-exportacao-nfstock.pack.alterdata.com.br`
   - **Código CRM**: `444807` (já vem preenchido)
   - **CNPJ**: o da empresa (com ou sem máscara)
   - **Endpoint Download**: deixe o default (já está com a URL nova)
   - **Token de Integração**: cole o Personal Access Token
3. Salvar APIs → toast verde "APIs salvas"
4. "🔎 Testar conexão" → valida token localmente (sem chamar Alterdata)
5. Auditor Fiscal → nova auditoria de 1 dia → os XMLs agora baixam de fato
6. Em caso de falha, `journalctl -u protheus-worker | grep "NFStock DEBUG"` mostra
   URL completa + headers mascarados + response preview

## ⏭️ Pós-hotfix

- Se a Alterdata expuser endpoint de health (`/api/v1/health`), aproveitar
  no `ping()` para validação real de rede (hoje é puramente local)
- Considerar cache de retry com backoff para o caso de 503 transitório do MS-Exportação

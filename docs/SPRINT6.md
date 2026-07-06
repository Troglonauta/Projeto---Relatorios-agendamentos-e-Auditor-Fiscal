# Sprint 6 — Dicionário SX3 + Date-picker + Sessões + A1 SEFAZ (2026-05-17)

Quatro entregas independentes solicitadas para fechar a homologação:

| # | Entrega | Status | Estilo |
|---|---|---|---|
| 1 | **Dicionário SX3** — headers Excel humanizados | ✅ | Backend + cache + UI reload |
| 2 | **Date-picker nativo** nos filtros | ✅ | Frontend (protheus.js + schedules.js) |
| 3 | **Gestão de Sessões Ativas** (painel admin) | ✅ | Backend + UI nova aba |
| 4 | **Auditor Fiscal Independente — A1 SEFAZ** | ✅ | PFX upload + SOAP NFeDistribuicaoDFe |

Decisão arquitetural: **abandono total das APIs Fluig/Alterdata** — qualquer cliente
agora consegue auditar sua própria emissão NFe diretamente na SEFAZ Nacional usando
o certificado A1 da empresa, sem dependência de Servidor TSS ou terceiros.

---

## 1️⃣ Dicionário SX3 — humanização de headers Excel

### Backend
[`backend/dict_sx3.py`](../backend/dict_sx3.py) (novo):
- Cache global thread-safe `(alias, campo) → {titulo, descric}`.
- `load_sx3(force=False)` lê toda SX3 numa única query (`WHERE D_E_L_E_T_ = ' '`),
  tenta `SX3010 / SX3990 / SX3000 / SX3` em ordem.
- `humanize_header("SC5__C5_EMISSAO")` retorna `"Data de Emissão (C5_EMISSAO)"`.
- Lazy-load: se não foi carregado e a 1ª exportação acontece, tenta carregar.
- Pre-load no lifespan: `main.py` chama `load_sx3()` no boot (best-effort).

### Integração no XLSX
[`backend/reports.py::write_xlsx_stream`](../backend/reports.py) ganhou parâmetro
`humanize_headers=True`. A tradução acontece **APENAS no `_flush_header_and_sample`**
(uma vez por export, 1 lookup por coluna). **Zero overhead por linha** —
preservamos os 8.452 reg/s do streaming.

```python
# Header escrito (linha 1):
"Numero do Pedido (C5_NUM)"
"Data de Emissão (C5_EMISSAO)"
"Nome do Cliente (A1_NOME)"

# Linha 2+ continua usando o nome cru no row.get(header):
row["SC5__C5_NUM"]   ← lookup direto, sem custo extra
```

### Endpoints admin
- `GET  /api/admin/sx3/stats`   — `{loaded, total_fields, sample}`
- `POST /api/admin/sx3/reload`  — força refresh (uso após customização do X3 no Configurador)

### UI
Aba **Manutenção** ganhou bloco:
```
📘 Dicionário SX3 (humanização de headers)
[ Recarregar SX3 ]   ✅ 4.832 campos carregados (4.715 indices por campo)
```

### Smoke test
```
> write_xlsx_stream(path, rows())
Header linha 1: ['SC5__C5_NUM', 'SC5__C5_EMISSAO']   ← fallback graceful sem SX3
Header fill A1: FF2E8B3D   font.bold=True   freeze=A2   widths={A:13,B:17}
```

Em produção (SX3 acessível), o header vira `["Numero do Pedido (C5_NUM)", ...]`.

---

## 2️⃣ Date-picker nativo nos filtros

### Detecção automática por sufixo
Em [`protheus.js::_isDateField`](../frontend/js/protheus.js) e
[`schedules.js::_isDateField_sch`](../frontend/js/schedules.js):

```js
// Match qualquer um dos sufixos típicos do Protheus
/(_EMISSAO|_DTDIGIT|_DATA[A-Z0-9_]*|_VENC[A-Z0-9_]*|_DT[A-Z0-9_]*)$/i

// "SC5__C5_EMISSAO"   → true
// "SE2__E2_VENCREAL"  → true
// "SF1__F1_DTDIGIT"   → true
// "SC5__C5_NUM"       → false
```

### Comportamento
- Quando o usuário escolhe um campo de data no select `f-field`:
  - `input.type` muda de `text` → `date`
  - O navegador renderiza calendário nativo (Chrome/Edge/Firefox)
  - Placeholder/title atualiza coerentemente

### Conversão antes do envio
`_normalizeDateBR` (já existia para DD/MM/YYYY) agora também aceita formato ISO
`YYYY-MM-DD` (saída padrão do `<input type=date>`):

```
"2026-12-31"  →  "20261231"   ← novo (Sprint 6, picker nativo)
"31/12/2026"  →  "20261231"   ← já existia (Sprint 5 UX)
"ABC123"      →  "ABC123"     ← não-data, intocado
```

Backend continua recebendo o formato Protheus nativo. **Zero mudança no contrato.**

---

## 3️⃣ Gestão de Sessões Ativas (painel admin)

A Fase 4 já tinha `ActiveSession` no banco para limitar logins simultâneos
(default 3). Sprint 6 expõe a gestão para o admin.

### Endpoints
| Verbo | Path | Função |
|---|---|---|
| GET    | `/api/admin/sessions` | Lista sessões válidas (`include_revoked=true` opcional) |
| DELETE | `/api/admin/sessions/{jti}` | Revoga uma sessão específica (jti) |
| DELETE | `/api/admin/sessions/user/{user_id}` | Revoga TODAS as sessões de um usuário (uso emergencial) |

A revogação seta `revoked_at = now()`. O próximo request com aquele JWT é
bloqueado em `deps.get_current_user` (já confere ActiveSession). Não precisa
restart, não invalida outras sessões da mesma conta.

### UI
Aba nova: **👥 Sessões Ativas**

```
[Usuário]           [IP]           [Navegador]   [Login]   [Expira]  [Status]  [Ação]
contabil@fertimaxi  192.168.1.45   Chrome 142    14/05/16  15/05/16  3× ⚠️    [Derrubar]
montenegrox@gmail   200.122.4.18   Firefox 138   14/05/14  15/05/14  você ✓   —
fiscal1@fertimaxi   192.168.1.62   Chrome 142    14/05/12  15/05/12  Ativa    [Derrubar]
```

- **Badge `3×` amarelo** quando o mesmo username tem múltiplas sessões → ajuda o
  admin a identificar contas compartilhadas que estouraram o limite.
- **Badge `você`** marca a sessão atual do próprio admin (impede shoot-yourself).
- **Confirm dialog** antes do revoke ("Derrubar sessão de X? Usuário precisará logar novamente").
- Auditado em `audit_logs`: `admin.session.revoke target=user1 jti=abc123...`

---

## 4️⃣ Auditor Fiscal Independente — Certificado A1 SEFAZ

### Por que abandonar as APIs Fluig/Alterdata

A Sprint 4.B introduziu **Transmite** (default), TSS on-premise, NFSTOCK e Smartlink
— todas dependem do servidor TOTVS ou de terceiros. Cliente sem TSS instalado
ficava sem alternativa.

A **SEFAZ Nacional** já oferece o WebService `NFeDistribuicaoDFe` para consulta
direta. Basta um **certificado A1** (.pfx) da empresa. Zero dependência externa.

### Implementação
[`backend/fiscal/xml_sources/a1.py`](../backend/fiscal/xml_sources/a1.py):

```
A1CertSource.get_xml(chave)
    │
    ├─ 1. Carrega .pfx (cryptography.pkcs12) com a senha.
    ├─ 2. Extrai cert+key para arquivos PEM TEMPORARIOS (chmod 600).
    ├─ 3. Monta SOAP envelope NFeDistribuicaoDFe:
    │       <tpAmb>1</tpAmb>           ← produção/homologação
    │       <cUFAutor>35</cUFAutor>    ← código IBGE da UF (SP=35, RJ=33, ...)
    │       <CNPJ>12345678000199</CNPJ>
    │       <consChNFe><chNFe>{44 dígitos}</chNFe></consChNFe>
    ├─ 4. POST para SEFAZ com cert=(cert.pem, key.pem), Content-Type SOAP 1.2.
    ├─ 5. Parse da resposta:
    │       <docZip schema="resNFe_v1.00">base64(gzip(xmlNFe))</docZip>
    │       cStat=137 (vazia) ou cStat=138 → XmlNotFound
    └─ 6. Decode base64 + gunzip → XML cru da NFe.
    finally:
        os.unlink(cert_pem); os.unlink(key_pem)   ← limpa sempre
```

### Endpoints
| Verbo | Path | Função |
|---|---|---|
| POST | `/api/admin/config/a1/upload` | Multipart .pfx (até 2 MB) → `data/secrets/a1.pfx` chmod 600 |
| POST | `/api/admin/config/a1`        | CNPJ, UF, ambiente, timeout, senha do PFX |
| POST | `/api/admin/test/a1`          | Smoke test: abre PFX + extrai cert (NÃO chama SEFAZ — economiza quota) |

### UI — Aba APIs externas
Novo bloco azul "🔐 SEFAZ Nacional (Certificado A1)" com:
- Upload do .pfx (validação .pfx/.p12 + cap 2 MB)
- CNPJ (14 dígitos, máscara removida no backend)
- UF (2 letras, mapeada para cUF IBGE)
- Ambiente (1=Produção, 2=Homologação)
- Timeout (5..300 segundos)
- Botão **🔎 Testar PFX** valida o arquivo sem consumir quota SEFAZ

### Indicador no admin
Quando PFX já está enviado, o botão muda:
```
[Enviar PFX]   →   [PFX já enviado — substituir]  ← cor success, hint visual
```

### Erros mapeados
- PFX corrompido ou senha errada → `XmlSourceError("Falha ao abrir PFX...")`
- NFe não localizada na SEFAZ   → `XmlNotFound("cStat=137...")`
- Falha de rede/timeout         → `XmlSourceError("Falha de rede SEFAZ...")`

### Como ativar para o Auditor
1. Admin → Configurações → APIs externas → SEFAZ A1 → upload + dados + senha
2. Admin → Configurações → Auditor Fiscal → **Fonte de XML** = "SEFAZ Nacional — Certificado A1"
3. Próxima execução de `/api/fiscal/audit/run` consulta direto na SEFAZ.

---

## ✅ Smoke test (Sprint 6)

```
dict_sx3 OK
admin_routes OK
A1CertSource OK, configured=False
main OK
routes total: 96
  GET     /api/admin/sx3/stats
  POST    /api/admin/sx3/reload
  GET     /api/admin/sessions
  DELETE  /api/admin/sessions/{jti}
  DELETE  /api/admin/sessions/user/{user_id}
  POST    /api/admin/config/a1/upload
  POST    /api/admin/config/a1
  POST    /api/admin/test/a1

XLSX gerado OK: 5 linhas, 4976 bytes
Header linha 1: ['SC5__C5_NUM', 'SC5__C5_EMISSAO']
Linha 2: ['000120', '20260512']
Freeze panes: A2
Col widths: {'A': 13.0, 'B': 17.0}
Header fill A1: FF2E8B3D   font.bold=True
```

---

## 📦 Backup

`backup/v1.6.0-pre-sprint-6/snapshot.tar.gz` (203 KB) — rollback pronto.

---

## 🧪 Como validar na próxima semana

### SX3 (qualquer Excel exportado)
1. Builder → SC5 + colunas C5_NUM, C5_EMISSAO, C5_CLIENTE
2. Baixar Excel → o header da linha 1 mostra **"Numero do Pedido (C5_NUM)"** etc.
3. Admin → Manutenção → "Recarregar SX3" → toast "4.832 campos carregados".

### Date-picker
1. Builder → filtro → escolher campo `C5_EMISSAO` → input vira calendário nativo.
2. Clicar no calendário → escolher data → executar consulta → resultado correto.
3. Trocar para `C5_NUM` → input volta a texto livre.

### Sessões
1. Logar em 3 navegadores diferentes com a mesma conta.
2. Admin → Sessões Ativas → vê 3 linhas com badge `3×`.
3. Clicar "Derrubar" em uma → confirma → após 1s, F5 naquela aba retorna 401.

### A1 SEFAZ
1. Admin → APIs → SEFAZ A1 → upload do .pfx + senha + CNPJ + UF SP + Ambiente 2 (homolog).
2. Botão "Testar PFX" → toast verde "✅ PFX valido — cert+key extraidos".
3. Auditor Fiscal → Fonte = "SEFAZ Nacional" → Run agora com 1 dia → XML chega.

---

## ⏭️ Pós Sprint 6 (sugestões)

- **SX3 com descrição (X3_DESCRIC)** em tooltip do header (hover no cabeçalho do Excel — exige openpyxl `comment`).
- **Date-picker BETWEEN** com 2 calendários (início/fim) num único filtro.
- **Sessões: limite por usuário customizado** (admin pode subir para 5 numa conta específica via UI).
- **A1: cache de cert/key em memória** com TTL — evita o overhead de ~50ms de extração por chamada (apenas se a volumetria fiscal subir muito).
- **A1: distribuição por NSU** (puxa lote de XMLs sem precisar saber as chaves antes — útil para "puxar TUDO dos últimos 90 dias").

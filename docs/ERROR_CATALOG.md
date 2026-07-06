# Catalogo de Erros — Protheus Reports

Todo erro retornado pela API contem um codigo no formato `ERR-XXX-NNN`. Use a
tabela abaixo para diagnosticar e resolver.

Resposta padrao da API em caso de erro:

```json
{
  "error_code": "ERR-DB-001",
  "message": "Falha ao conectar no banco Protheus",
  "detail": "DSN nao localizada para host=SEU_HOST_PROTHEUS"
}
```

Codigos sao definidos em [`backend/errors.py`](../backend/errors.py).

---

## DB — Banco Protheus (SQL Server)

| Codigo | Mensagem | HTTP | Causa provavel | Resolucao |
|---|---|---|---|---|
| ERR-DB-001 | Falha ao conectar no banco Protheus | 502 | Host/porta errados, firewall, banco offline | Valide `PROTHEUS_DB_URL` no Wizard; teste com `Testar conexao` |
| ERR-DB-002 | Driver ODBC nao encontrado | 500 | `msodbcsql17` ausente | Instale o ODBC Driver 17 (Windows: link Microsoft; LXC Debian/Ubuntu: ver `docs/DEPLOY_LXC.md` §2.2) |
| ERR-DB-003 | Login Protheus negado pelo servidor | 502 | Usuario/senha errados, conta bloqueada | Troque as credenciais no Wizard / Admin |
| ERR-DB-004 | Timeout na consulta Protheus | 504 | Query muito longa, tabela bloqueada | Reduza periodo do filtro; aumente `PROTHEUS_POOL_TIMEOUT` |
| ERR-DB-005 | Pool de conexoes Protheus exaurido | 503 | Concorrencia maior que o pool | Aumente `PROTHEUS_POOL_SIZE`/`PROTHEUS_MAX_OVERFLOW` |

## AUTH — Autenticacao e permissao

| Codigo | Mensagem | HTTP | Causa provavel | Resolucao |
|---|---|---|---|---|
| ERR-AUTH-001 | Usuario ou senha invalidos | 401 | Credenciais erradas | Pedir reset de senha |
| ERR-AUTH-002 | Token JWT expirado | 401 | Sessao passou de `JWT_EXPIRE_MINUTES` | Fazer login novamente |
| ERR-AUTH-003 | Sem permissao para esta tabela | 403 | Tabela nao esta na whitelist do usuario | Admin adiciona em Usuarios > Permissoes |
| ERR-AUTH-004 | Sem permissao para esta acao | 403 | Operador sem `export` / `schedule` | Admin libera em Usuarios > Acoes |
| ERR-AUTH-005 | Sessao encerrada por inatividade | 401 | Usuario ficou parado > `SESSION_IDLE_MINUTES` | Fazer login novamente |

## SMTP — Envio de e-mail

| Codigo | Mensagem | HTTP | Causa provavel | Resolucao |
|---|---|---|---|---|
| ERR-SMTP-001 | Servidor SMTP inalcancavel | 502 | Host/porta errados, firewall bloqueado | Validar com `Testar SMTP` no Wizard |
| ERR-SMTP-002 | Credenciais SMTP rejeitadas | 502 | Senha errada, SMTP AUTH desabilitado | Habilitar SMTP AUTH no tenant; ver senha de app (M365) |
| ERR-SMTP-003 | STARTTLS recusado pelo servidor | 502 | Porta 465 sem TLS direto, ou 587 sem STARTTLS | Trocar `SMTP_USE_TLS` |
| ERR-SMTP-004 | Relay negado — verifique o remetente autorizado | 502 | Endereco `From` nao bate com a caixa de envio | Use `SMTP_FROM = SMTP_USER` |
| ERR-SMTP-005 | Anexo excede o limite do servidor SMTP | 413 | Excel > 25MB (Office 365 limite) | Use formato CSV para datasets grandes |

## JOB — Fila Celery / Worker

| Codigo | Mensagem | HTTP | Causa provavel | Resolucao |
|---|---|---|---|---|
| ERR-JOB-001 | Fila indisponivel — broker offline | 503 | Redis fora, ou SQLite broker travado | Verificar Redis (prod) ou apagar `data/celery_broker.db` (dev) |
| ERR-JOB-002 | Job perdido — worker reiniciado durante execucao | 500 | `kill -9` no worker | Re-enviar o job — `reset_orphan_jobs` ja marcou |
| ERR-JOB-003 | Job cancelado pelo usuario | 409 | Botao "Cancelar" clicado | Comportamento esperado |
| ERR-JOB-004 | Dataset excede o limite do formato | 413 | XLSX > 1.048.575 linhas | Use CSV para datasets maiores |
| ERR-JOB-005 | Erro critico no worker | 500 | Exception inesperada (ver `error_detail`) | Investigar log do worker |
| ERR-JOB-006 | Payload de job invalido | 422 | JSON malformado ou campo obrigatorio ausente | Reenviar com payload correto |

## PROTHEUS — Logica de tabelas

| Codigo | Mensagem | HTTP | Causa provavel | Resolucao |
|---|---|---|---|---|
| ERR-PROTHEUS-001 | Tabela Protheus nao existe | 404 | Alias+filial+sufixo nao bate nenhuma tabela | Confira em `db-tables` (admin) |
| ERR-PROTHEUS-002 | Coluna invalida — caracteres nao permitidos | 400 | Coluna com espaco ou simbolos | Use somente `[A-Z0-9_]` no Builder |
| ERR-PROTHEUS-003 | Filtro malformado | 400 | Operador desconhecido | Use operadores do dropdown do Builder |

## FISCAL — Auditor Fiscal (Sprint 3)

| Codigo | Mensagem | HTTP | Causa provavel | Resolucao |
|---|---|---|---|---|
| ERR-FISCAL-001 | Fonte XML nao configurada | 412 | TSS/A1/NFSTOCK sem credenciais | Preencher no Wizard `APIs externas` |
| ERR-FISCAL-002 | XML nao encontrado na fonte (NFe nao distribuida) | 404 | NFe ainda nao autorizada SEFAZ, ou cancelada | Aguardar autorizacao; verificar status da NFe |
| ERR-FISCAL-003 | Chave de acesso NFe invalida (44 digitos) | 400 | Chave truncada ou com caracteres nao numericos | Verificar `F1_CHVNFE` no Protheus |
| ERR-FISCAL-004 | Certificado A1 invalido ou expirado | 500 | `.pfx` corrompido, senha errada, fora da validade | Renovar certificado A1; reupload no Admin |
| ERR-FISCAL-005 | Falha generica no Auditor Fiscal | 500 | Exception nao mapeada | Ver log do worker fiscal |
| ERR-FISCAL-006 | NCM divergente entre XML e cadastro SB1 (compliance) | 422 | NCM do XML difere do `SB1.B1_POSIPI` do produto. **Risco fiscal direto.** | Verificar cadastro do produto OU contestar com fornecedor — corrigir ANTES do fechamento fiscal |

## CFG — Configuracao e master key

| Codigo | Mensagem | HTTP | Causa provavel | Resolucao |
|---|---|---|---|---|
| ERR-CFG-001 | MASTER_KEY invalida ou ausente | 500 | `.env` corrompido ou chave nao e' base64 Fernet | Restaurar do backup; em ultimo caso, gerar nova com `python -m backend.cli.rotate_master_key` |
| ERR-CFG-002 | Setting solicitado nao encontrado | 404 | Chave nao migrada do `.env`, Wizard incompleto | Editar via Admin |
| ERR-CFG-003 | Hot-reload de configuracao falhou | 500 | Erro durante `engine.reset()` ou scheduler restart | Ver log do uvicorn; ultima opcao: restart manual |

---

## Lookup rapido por sintoma

| Sintoma na UI | Provavel codigo |
|---|---|
| Toast "Falha no Protheus" no Builder | `ERR-DB-001` ou `ERR-DB-003` |
| Toast "Fila indisponivel" no botao Exportar grande | `ERR-JOB-001` |
| E-mail nao chega | `ERR-SMTP-001..004` |
| Wizard nao consegue salvar SMTP | Geralmente nao e' AppError; ve log uvicorn |
| Auditor Fiscal nao processa | `ERR-FISCAL-001` (sem credenciais) ou `ERR-FISCAL-002` (NFe sem XML) |
| Apos trocar MASTER_KEY, login falha | `ERR-CFG-001` — credenciais cifradas viraram lixo |

## Como adicionar um codigo novo

1. Adicione um `ErrorSpec` em `backend/errors.py`.
2. Inclua no dict `_BY_CODE`.
3. Documente nesta tabela (camada + codigo + mensagem + causa + resolucao).
4. Use no codigo via `raise AppError(ERR_XXX_NNN, detail="...")`.

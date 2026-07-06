# Sprint 4.A — Perfis & Categorização de Tabelas (entregue 2026-05-14)

## Resumo executivo

Sistema de perfis (módulos) implementado de ponta-a-ponta. O **Builder Visual
agora filtra tabelas pelos módulos do usuário corrente**, e a gestão de
permissões deixou de ser por whitelist de tabelas para ser por **perfis** —
mais escalável e alinhada à organização da Fertimaxi.

## O que mudou

### Backend

| Arquivo | Função |
|---|---|
| [backend/profiles_seed.py](../backend/profiles_seed.py) | 8 perfis canônicos + matriz default (80 aliases mapeados em 102 associações) |
| [backend/models.py](../backend/models.py) | 3 modelos novos: `Profile`, `TableProfile`, `UserProfile` (todos com cascade e unique constraints) |
| [backend/schemas.py](../backend/schemas.py) | `ProfileCreate/Update/Out`, `TableAssign`, `UserProfilesUpdate`; `UserOut` ganhou `allowed_profiles` |
| [backend/routers/profiles_routes.py](../backend/routers/profiles_routes.py) | CRUD perfis + matriz tabela×perfil + perfis-de-user |
| [backend/deps.py](../backend/deps.py) | `assert_table_allowed` agora aceita perfis (OR com `UserTablePermission`) |
| [backend/routers/protheus_routes.py](../backend/routers/protheus_routes.py) | `/aliases` retorna `profiles[]` por alias + `user_profiles[]` + filtra para operadores |
| [backend/routers/users_routes.py](../backend/routers/users_routes.py) | `UserOut.allowed_profiles`; create/update sincroniza `UserProfile` |
| [backend/main.py](../backend/main.py) | Seed no lifespan + registra `profiles_routes.router` + `user_profiles_router` |

**Endpoints novos (todos admin-only):**
- `GET    /api/profiles`
- `POST   /api/profiles`
- `PUT    /api/profiles/{id}`
- `DELETE /api/profiles/{id}`
- `GET    /api/profiles/{id}/tables`
- `POST   /api/profiles/{id}/tables`
- `DELETE /api/profiles/{id}/tables/{alias}`
- `GET    /api/users/{user_id}/profiles`
- `PUT    /api/users/{user_id}/profiles`

### Frontend

| Arquivo | Função |
|---|---|
| [frontend/pages/profiles.html](../frontend/pages/profiles.html) + [frontend/js/profiles.js](../frontend/js/profiles.js) | **Página nova** — lista de perfis à esquerda + matriz de tabelas à direita; clique numa tabela toggle a associação em tempo real |
| [frontend/js/users.js](../frontend/js/users.js) | Coluna "Perfis" na listagem (badges); checkboxes de perfis no modal create/edit; envia `allowed_profiles` no payload |
| [frontend/js/protheus.js](../frontend/js/protheus.js) | Dropdown **"Módulo"** no topo do Builder filtra os aliases mostrados; aliases trazem `[CONTABIL,FINANCEIRO]` no label |
| [frontend/js/layout.js](../frontend/js/layout.js) | Sidebar admin ganhou item "Perfis & Módulos" |

### Regras de acesso (Fase 4.A)

Operador acessa um alias do Protheus SE:
```
   (alias está em UserTablePermission direta)   ← whitelist tradicional, ainda existe
   OU
   (alias está em algum TableProfile cujo Profile está em UserProfile do user)
```

Admin sempre passa. A whitelist direta serve agora para **exceções pontuais**
(uma tabela específica para um operador específico sem precisar criar perfil).

## Matriz default (80 aliases mapeados)

Os 8 perfis seeded receberam estas distribuições iniciais:

| Perfil | Quantidade | Exemplos |
|---|---|---|
| LOGISTICA | 19 | SA4, DA4, Z00, ZA4, ZA6, ZC8, ZAQ, ZAM… |
| FINANCEIRO | 11 | SA2, SE1, SE2, SE5, SA6, FK5, FK6, SE3, SE4… |
| ESTOQUE | 13 | SB1, SB5, SB6, SB7, SB8, SD3, SD4, SA5, ZC0… |
| PCP | 11 | SC2, SC3, SC7, SC8, SCB, SCH, SCP, SCY, SD6, SD0, SAJ |
| CONTABIL | 10 | CT2, CT5, SF1, SF2, SF3, SF4, SD1, ZF4, ZC1, SCH |
| CONTROLADORIA | 6 | SF1, SD1, SD2, SCH, ZF4 (overlap intencional com CONTABIL) |
| COMERCIAL | 16 | SA1, SA3, SC5, SC6, SC9, SD2, SE3, SF2, Z59, Z62, Z2C, Z2D, ZAM, ADA, ADB, SAB |
| ADMINISTRATIVO | 13 | SAI, SAK, SAL, SAU, SCR, SCS, SCV, SZ3, SAJ, SAH, SF4, SF5, SE4, Z62 |

(Aliases podem aparecer em múltiplos perfis — total de 102 linhas em `table_profiles`.)

## Backwards compatibility

- `UserTablePermission` **continua existindo** e funcionando.
- Usuários da Fase 3 continuam acessando suas tabelas via whitelist direta.
- Quando o admin vincular esse usuário a um perfil, o gate passa a ser OR (whitelist + perfis).
- Recomendação: criar uma carga de migração futura que converte `UserTablePermission` para `UserProfile` quando possível.

## Smoke test passou

```
seed primeira vez : {'profiles_created': 8, 'matrix_rows': 102}
seed idempotente  : {'profiles_created': 0, 'matrix_rows': 0}
total de rotas    : 81 (era 71 antes da Sprint 4.A)
```

## Como testar

1. `python scripts\start.py` — sobe Web + Worker.
2. Login como admin.
3. Sidebar → **Perfis & Módulos** → confira os 8 perfis com tabelas pré-associadas.
4. Sidebar → **Usuários** → editar um operador → marcar perfis → salvar.
5. Logar como esse operador → sidebar → **Consultas Protheus** → dropdown "Módulo"
   filtra os aliases; tente acessar uma tabela fora do módulo → recebe 403.

## Próxima sprint — 4.B (Transmite + 8 tabelas)

Já anotado em `memory/fiscal_sprint_4b_notes.md`:

- **Tolerância R$ 0,01 a R$ 0,05** em rateios de frete/imposto (configurável por AppSetting).
- **Validador forte de NCM** (XML × SB1.B1_POSIPI) — severidade **critical** sempre, **sem tolerância**.
- Novo código de erro `ERR-FISCAL-006`: "NCM divergente entre XML e SB1".
- Seção destacada (vermelha) no e-mail HTML para anomalias de NCM.

<p align="center">
  <img src="fertimaxi_icon.png" alt="Fertimaxi" width="90">
</p>

<h1 align="center">Protheus Reports &amp; Auditor Fiscal</h1>

<p align="center">
  Relatórios do <b>TOTVS Protheus</b>, agendamentos por e-mail e auditoria fiscal —
  cruzando o documento lançado no ERP com o XML importado.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/Celery-37814A?logo=celery&logoColor=white" alt="Celery">
  <img src="https://img.shields.io/badge/SQLAlchemy-D71F00?logo=sqlalchemy&logoColor=white" alt="SQLAlchemy">
  <img src="https://img.shields.io/badge/Bootstrap%205-7952B3?logo=bootstrap&logoColor=white" alt="Bootstrap 5">
  <img src="https://img.shields.io/badge/TOTVS-Protheus-FF6B00" alt="TOTVS Protheus">
  <img src="https://img.shields.io/badge/licen%C3%A7a-uso%20interno-lightgrey" alt="Licença">
</p>

---

Plataforma interna da **Fertimaxi** para extrair relatórios do **TOTVS Protheus**,
agendar envios por e-mail e **auditar notas fiscais** cruzando o documento
lançado no ERP com o XML importado.

Principais recursos:

- **Construtor Visual de Consultas** — monta a consulta sem digitar SQL nem o
  nome físico da tabela; escolhe módulo, tabela (alias), filial, colunas
  (humanizadas via dicionário SX3), filtros e relacionamentos (JOIN).
- **Agendamentos** — envio automático de relatórios por e-mail em periodicidade
  configurável (diária/semanal/mensal), processado em segundo plano.
- **Fila assíncrona (Celery)** — relatórios pesados (centenas de milhares de
  linhas) são gerados em background, com progresso e download, sem travar a tela.
- **Auditor Fiscal** — compara, campo a campo, o documento lançado no ERP
  (SF1/SD1) com o XML importado (SDS/SDT): número, série, emissão, CNPJ, valores,
  CFOP, CST, ICMS por item, descrição do produto, etc. Trabalha por **decêndio**
  (períodos de 10 dias), guarda o histórico de cada auditoria, permite marcar
  status manualmente por campo (com trilha de quem alterou) e filtrar por tipo de
  cruzamento e por conformidade.
- **Controle de acesso** — papéis (admin/operador), perfis por módulo e
  permissões granulares por ação (visualizar, exportar, agendar, auditar), além
  de trilha de auditoria de tudo que acontece.
- **Segurança** — JWT com limite de sessões simultâneas, logout por inatividade e
  credenciais sensíveis cifradas (Fernet) no banco.

> Stack: **FastAPI · SQLAlchemy · Celery · APScheduler · pyodbc · openpyxl ·
> Bootstrap 5 · DataTables · Chart.js**

---

## Telas

As capturas de tela ficam em [`docs/screenshots/`](docs/screenshots/). Para
exibi-las aqui, basta salvar os PNGs nessa pasta com os nomes abaixo e
descomentar o bloco:

<!-- Descomente após adicionar as imagens em docs/screenshots/
<p align="center">
  <img src="docs/screenshots/auditor.png" alt="Auditor Fiscal" width="85%"><br><br>
  <img src="docs/screenshots/consultas.png" alt="Construtor de Consultas" width="85%"><br><br>
  <img src="docs/screenshots/dashboard.png" alt="Dashboard" width="85%">
</p>
-->

---

## Como rodar (desenvolvimento)

Pré-requisitos: Python 3.11+, ODBC Driver 17 for SQL Server (para conectar ao
Protheus) e as dependências do `requirements.txt`.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env              # e preencha os valores reais
python scripts\start.py
```

O `scripts/start.py` sobe **Web + Worker no mesmo terminal** (o broker do Celery
cai para SQLite automaticamente quando não há Redis). Acesse
`http://localhost:8000/`.

Na primeira execução, a aplicação redireciona para o **Setup Wizard**, onde se
configura banco Protheus, SMTP, identidade visual e o usuário administrador
inicial — depois disso, tudo é editável pela aba **Administração**.

## Produção

Servidor Linux com `systemd` (serviços web e worker) + Nginx na porta 80.
Passo a passo em [`docs/DEPLOY_LXC.md`](docs/DEPLOY_LXC.md).

---

## Estrutura

```
protheus-reports/
├── backend/
│   ├── main.py                # FastAPI + lifespan (setup wizard, migrações leves)
│   ├── config.py              # Settings via .env
│   ├── database.py/models.py  # Banco local (usuários, jobs, anomalias, decisões…)
│   ├── auth.py / deps.py      # JWT + limite de sessões + RBAC
│   ├── protheus_api.py        # Pool de conexão ao SQL Server do Protheus
│   ├── dict_sx3.py            # Humanização de colunas pelo dicionário SX3
│   ├── reports.py             # Geração de XLSX/CSV (streaming) + humanização
│   ├── scheduler.py           # APScheduler (agendamentos + auditoria fiscal)
│   ├── security/              # Fernet + settings cifrados
│   ├── fiscal/                # Auditor Fiscal
│   │   ├── rule_engine.py     # Motor de regras (cruzamentos campo a campo)
│   │   ├── internal_audit.py  # Carga SDS/SDT × SF1/SD1 do Protheus
│   │   ├── auditor.py         # Orquestração da auditoria + persistência
│   │   ├── comparators.py     # Comparadores puros (tolerâncias configuráveis)
│   │   ├── finance_audit.py   # Motor financeiro (SF1 × SE2)
│   │   └── commercial_audit.py# Motor comercial (SC5 × SE1)
│   ├── queue/                 # Celery app + tasks (relatórios e auditoria)
│   └── routers/               # auth, users, profiles, protheus, schedules,
│                              # jobs, fiscal, dashboard, admin, audit, setup…
├── frontend/
│   ├── pages/                 # login, setup, dashboard, protheus, schedules,
│   │                          # users, audit, fiscal, profiles, admin
│   ├── js/                    # módulos ES6
│   └── css/                   # tema Fertimaxi
├── scripts/                   # start.py (dev), seed_admin.py, supervisor.py
├── docs/                      # manuais e histórico
├── data/                      # volume local (SQLite, branding) — não versionado
└── reports_output/            # arquivos gerados pelo worker — não versionado
```

---

## Backup obrigatório

Faça backup conjunto de:

```
.env            ← contém a MASTER_KEY (chave que decifra as credenciais)
data/app.db     ← usuários, jobs, anomalias e settings cifrados
data/branding/  ← identidade visual
```

**Perder a `MASTER_KEY` = perder todas as credenciais cifradas.** Para rotacionar:

```bash
.venv/bin/python -m backend.cli.rotate_master_key --dry-run
.venv/bin/python -m backend.cli.rotate_master_key
```

---

## Segurança / configuração

Nenhum segredo é versionado. Use `.env` (a partir do `.env.example`) para as
credenciais reais — `.env`, `.env.prod`, chaves e certificados ficam fora do
controle de versão (ver `.gitignore`).

## Licença

Uso interno — Fertimaxi Comércio. Todos os direitos reservados. Ver
[`LICENSE`](LICENSE).

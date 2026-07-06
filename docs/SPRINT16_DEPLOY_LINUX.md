# Sprint 16 — Deploy Produção Linux (Proxmox VM)

**v2.6.0 · 2026-05-28**

Documento definitivo de arquitetura e deploy bare-metal do **Protheus Reports
(Auditor Fiscal)** em VM Linux dentro do cluster Proxmox da Fertimaxi.

> Público-alvo: Gestor de Infra / equipe DevOps responsável pelo provisionamento.
> Pressuposto: VM já criada no Proxmox com Ubuntu Server 24.04 LTS instalado e
> acesso `sudo` ao usuário operacional (ex: `infraadm`).

---

## Sprint 16 — Checklist executivo

Execute na ordem. Cada passo é **idempotente** (pode rodar de novo sem quebrar).

- [ ] **F1.** Provisionar VM no Proxmox (sizing tópico 1)
- [ ] **F2.** Configurar firewall Proxmox + iptables (tópico 1)
- [ ] **F3.** Instalar SO base, Python, Redis, MS ODBC (tópico 2)
- [ ] **F4.** Criar usuário de serviço, estrutura de pastas, venv (tópico 3)
- [ ] **F5.** Subir código da aplicação (FTP/WinSCP/`git pull`) em `/opt/protheus-reports/app`
- [ ] **F6.** Preencher `/opt/protheus-reports/app/.env` (tópico 3.5)
- [ ] **F7.** Instalar e habilitar units systemd: web, worker, beat (tópico 4)
- [ ] **F8.** Configurar Nginx como proxy reverso (tópico 5)
- [ ] **F9.** Validar smoke test (`curl /health`, login, primeira auditoria)
- [ ] **F10.** Publicar `update.sh` em `/opt/protheus-reports/` (tópico 6)
- [ ] **F11.** Configurar backup diário de `/opt/protheus-reports/data/` (tópico 7)

---

## 1. Requisitos da VM Proxmox e Rede

### 1.1 Sizing recomendado

| Recurso       | Mínimo | **Recomendado** | Observação |
|---|---|---|---|
| vCPU          | 2     | **4**          | Worker Celery + uvicorn + APScheduler. Auditorias pesadas usam todos os cores |
| RAM           | 4 GB  | **8 GB**       | pandas+openpyxl com 100k linhas usa ~1.5 GB de pico |
| Disco (SSD)   | 30 GB | **80 GB SSD**  | OS ~10 GB · venv ~1 GB · `data/` (app.db) ~5 GB · `reports_output/` cresce ~500 MB/mês |
| Disco extra   | —     | **+50 GB**     | Opcional — montar em `/var/backups/protheus-reports` |
| Rede          | 1 Gbps | **1 Gbps**    | Latência baixa até o SQL Server do Protheus é crítica |
| OS Template   | —     | **Ubuntu Server 24.04 LTS** | (22.04 LTS também suportado) |

> **Importante:** marcar a VM no Proxmox como **start at boot** e configurar
> `QEMU Guest Agent: enabled` para shutdown gracioso.

### 1.2 Portas e Firewall

#### Inbound (entrada — quem chega na VM)

| Porta | Protocolo | Origem                      | Destino na VM    | Para quê |
|------|------|------|------|------|
| 22   | TCP  | Rede de admin (`10.x.x.0/24`) | sshd            | Acesso SSH/SFTP |
| 80   | TCP  | Toda a rede interna         | Nginx → 8000    | UI HTTP do Auditor Fiscal |
| 443  | TCP  | Toda a rede interna         | Nginx → 8000    | UI HTTPS (após certificado interno) |

#### Outbound (saída — a VM se conecta)

| Porta    | Protocolo | Destino                          | Para quê |
|------|------|------|------|
| **1433** | TCP  | SQL Server Protheus (`sqlsrv.fertimaxi.lan`) | Leitura via pyodbc (T-SQL) |
| 587      | TCP  | SMTP corporativo                 | E-mails do Auditor (STARTTLS) |
| 465      | TCP  | SMTP corporativo (alternativo)   | SSL implícito |
| 53       | UDP  | DNS interno                      | Resolução `sqlsrv.fertimaxi.lan` |
| 80/443   | TCP  | Mirrors Ubuntu + PyPI            | `apt update` / `pip install` (pode fechar pós-deploy) |

#### Internas (loopback — não saem da VM)

| Porta | Componente | Notas |
|------|------|------|
| 8000 | Uvicorn (FastAPI)  | Bind `127.0.0.1` quando Nginx estiver na frente |
| 6379 | Redis (Celery broker) | Bind `127.0.0.1` apenas |

#### Regra Proxmox/iptables (Datacenter → Firewall)

```bash
# No próprio host Proxmox, regra de segurança em nível datacenter
# Aplique também nas opções da VM (Firewall → Yes).
# Exemplo: aplicar apenas em interface vmbr0 (rede interna)
```

---

## 2. Sistema Operacional e Pacotes Base

> Todos os comandos a partir daqui assumem **Ubuntu Server 24.04 LTS** (testado
> também em 22.04). Rodar como usuário com `sudo`.

### 2.1 Atualização do SO

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl gnupg ca-certificates lsb-release \
    build-essential git pkg-config locales tzdata
sudo timedatectl set-timezone America/Sao_Paulo
sudo locale-gen pt_BR.UTF-8
```

### 2.2 Python 3.12 + venv + pip

Ubuntu 24.04 já vem com Python 3.12 nativo. Instale só os módulos que faltam:

```bash
sudo apt install -y python3 python3-venv python3-pip python3-dev
python3 --version   # esperado: 3.12.x
```

### 2.3 Microsoft ODBC Driver 18 for SQL Server + unixodbc-dev (CRÍTICO)

Sem isso, `pyodbc` quebra com `Can't open lib 'ODBC Driver 18 for SQL Server'`.

```bash
# 1) Importa a chave GPG da Microsoft
curl -sSL https://packages.microsoft.com/keys/microsoft.asc | \
  sudo gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg

# 2) Adiciona o repo (Ubuntu 24.04). Para 22.04, troque "24.04" por "22.04".
curl https://packages.microsoft.com/config/ubuntu/24.04/prod.list | \
  sudo tee /etc/apt/sources.list.d/mssql-release.list

# 3) Instala driver + dev headers
sudo apt update
sudo ACCEPT_EULA=Y apt install -y msodbcsql18 unixodbc-dev mssql-tools18

# 4) Adiciona sqlcmd ao PATH (opcional, ajuda em troubleshooting)
echo 'export PATH="$PATH:/opt/mssql-tools18/bin"' | sudo tee -a /etc/profile.d/mssql.sh
```

**Validação rápida:**

```bash
odbcinst -q -d        # deve listar "ODBC Driver 18 for SQL Server"
sqlcmd -S sqlsrv.fertimaxi.lan -U usuario -P senha -Q "SELECT @@VERSION" -C
```

### 2.4 Redis (broker do Celery)

```bash
sudo apt install -y redis-server
sudo systemctl enable --now redis-server
# Confirme bind apenas em localhost (segurança):
sudo sed -i 's/^bind .*/bind 127.0.0.1 -::1/' /etc/redis/redis.conf
sudo systemctl restart redis-server
redis-cli ping   # esperado: PONG
```

### 2.5 Nginx (proxy reverso)

```bash
sudo apt install -y nginx
sudo systemctl enable --now nginx
```

---

## 3. Preparação do Ambiente da Aplicação

### 3.1 Estrutura de pastas oficial

```
/opt/protheus-reports/
├── app/                  # código da aplicação (release atual)
│   ├── backend/
│   ├── frontend/
│   ├── requirements.txt
│   ├── run.py
│   └── .env              # NÃO versionado — segredos
├── venv/                 # virtualenv Python (isolado do SO)
├── data/                 # SQLite + branding + secrets (PERSISTENTE — backup!)
│   ├── app.db
│   ├── branding/logo.png
│   └── secrets/
├── reports_output/       # XLSX/CSV gerados pelo worker
├── logs/                 # logs rotacionados (opcional, systemd já vai pra journald)
└── update.sh             # script de atualização (tópico 6)
```

### 3.2 Usuário de serviço e permissões

A app NÃO pode rodar como `root`. Criamos um usuário de sistema dedicado.

```bash
# Cria grupo + usuário de sistema sem shell (segurança)
sudo groupadd --system protheus
sudo useradd --system --gid protheus --shell /usr/sbin/nologin \
    --home-dir /opt/protheus-reports --no-create-home protheus

# Estrutura
sudo mkdir -p /opt/protheus-reports/{app,venv,data,data/branding,data/secrets,reports_output,logs}
sudo chown -R protheus:protheus /opt/protheus-reports
sudo chmod 750 /opt/protheus-reports
sudo chmod 770 /opt/protheus-reports/data /opt/protheus-reports/reports_output
sudo chmod 700 /opt/protheus-reports/data/secrets
```

### 3.3 Subir o código

Opção A — via SFTP/WinSCP (entrega manual):

```bash
# Após receber o conteúdo do projeto no /tmp/protheus-reports.tar.gz:
sudo tar -xzf /tmp/protheus-reports.tar.gz -C /opt/protheus-reports/app --strip-components=1
sudo chown -R protheus:protheus /opt/protheus-reports/app
```

Opção B — via `git clone` (se houver repositório):

```bash
sudo -u protheus git clone <URL> /opt/protheus-reports/app
```

### 3.4 Criar venv e instalar dependências

```bash
sudo -u protheus python3 -m venv /opt/protheus-reports/venv
sudo -u protheus /opt/protheus-reports/venv/bin/pip install --upgrade pip wheel
sudo -u protheus /opt/protheus-reports/venv/bin/pip install \
    -r /opt/protheus-reports/app/requirements.txt
# Adicional para produção (gunicorn como worker class do uvicorn):
sudo -u protheus /opt/protheus-reports/venv/bin/pip install gunicorn
```

### 3.5 Arquivo `.env` de produção

> Crie em `/opt/protheus-reports/app/.env` com **permissão 600** e dono
> `protheus:protheus`. Este arquivo guarda segredos — nunca commitar.

```bash
sudo -u protheus tee /opt/protheus-reports/app/.env >/dev/null <<'EOF'
# === Aplicação ===
APP_NAME=Protheus Reports — Fertimaxi
APP_ENV=production
APP_HOST=127.0.0.1
APP_PORT=8000
APP_TIMEZONE=America/Sao_Paulo

# === Segurança ===
# JWT_SECRET: gere com: openssl rand -hex 48
JWT_SECRET=__GERAR_COM_openssl_rand_hex_48__
JWT_EXPIRE_MINUTES=480
SESSION_IDLE_MINUTES=20

# MASTER_KEY: gere com: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# CRÍTICO: backup desta chave — sem ela, todos os segredos no AppSetting ficam ilegíveis.
MASTER_KEY=__GERAR_COM_Fernet_generate_key__

# === Banco local da aplicação ===
DATABASE_URL=sqlite:////opt/protheus-reports/data/app.db

# === Conexão Protheus (SQL Server) ===
PROTHEUS_DB_URL=mssql+pyodbc://USUARIO:SENHA_URL_ENCODED@sqlsrv.fertimaxi.lan:1433/PROTHEUS?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=no&TrustServerCertificate=yes
PROTHEUS_TABLE_SUFFIX=0

# === SMTP ===
SMTP_HOST=smtp.fertimaxi.com.br
SMTP_PORT=587
SMTP_USER=auditor@fertimaxi.com.br
SMTP_PASSWORD=__SENHA_SMTP__
SMTP_FROM=auditor@fertimaxi.com.br
SMTP_USE_TLS=True

# === Celery / Redis ===
QUEUE_BROKER_URL=redis://localhost:6379/0
QUEUE_RESULT_BACKEND=redis://localhost:6379/0

# === Output e scheduler ===
REPORTS_OUTPUT_DIR=/opt/protheus-reports/reports_output
SCHEDULER_INTERVAL_MINUTES=60
EOF
sudo chmod 600 /opt/protheus-reports/app/.env
sudo chown protheus:protheus /opt/protheus-reports/app/.env
```

**Atenção a 2 chaves geradas localmente:**

```bash
# JWT_SECRET (48 bytes hex)
openssl rand -hex 48

# MASTER_KEY (Fernet 32 bytes base64)
sudo -u protheus /opt/protheus-reports/venv/bin/python -c \
    "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Cole os valores no `.env`. **Armazene cópia desses dois valores no cofre da Fertimaxi**
— a `MASTER_KEY` é o que protege todos os segredos no SQLite (`AppSetting`).
Se perdê-la, é preciso recriar todas as configurações.

---

## 4. Configuração dos Serviços (systemd)

Três units. Todos rodam como usuário `protheus`, leem `.env` automaticamente,
reiniciam em caso de falha e sobem no boot.

### 4.1 Serviço Web (Uvicorn + Gunicorn workers)

`/etc/systemd/system/protheus-reports-web.service`

```ini
[Unit]
Description=Protheus Reports — Web (FastAPI/Uvicorn via Gunicorn)
After=network.target redis-server.service
Wants=redis-server.service

[Service]
Type=simple
User=protheus
Group=protheus
WorkingDirectory=/opt/protheus-reports/app
EnvironmentFile=/opt/protheus-reports/app/.env
Environment="PYTHONUNBUFFERED=1"
Environment="PATH=/opt/protheus-reports/venv/bin"

# Gunicorn com UvicornWorker — 4 workers (ajustar = (2 * vCPU) + 1 com cap em 8)
ExecStart=/opt/protheus-reports/venv/bin/gunicorn backend.main:app \
    --workers 4 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 127.0.0.1:8000 \
    --timeout 120 \
    --keep-alive 5 \
    --access-logfile - \
    --error-logfile - \
    --log-level info

# Resiliência
Restart=on-failure
RestartSec=5
StartLimitIntervalSec=60
StartLimitBurst=5

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/protheus-reports/data /opt/protheus-reports/reports_output /opt/protheus-reports/logs

[Install]
WantedBy=multi-user.target
```

### 4.2 Serviço Worker (Celery)

`/etc/systemd/system/protheus-reports-worker.service`

```ini
[Unit]
Description=Protheus Reports — Celery Worker (auditoria + relatórios pesados)
After=network.target redis-server.service protheus-reports-web.service
Requires=redis-server.service

[Service]
Type=simple
User=protheus
Group=protheus
WorkingDirectory=/opt/protheus-reports/app
EnvironmentFile=/opt/protheus-reports/app/.env
Environment="PYTHONUNBUFFERED=1"
Environment="PATH=/opt/protheus-reports/venv/bin"

# Concurrency = vCPU (ajustar conforme carga). Em Linux usar pool=prefork (default).
ExecStart=/opt/protheus-reports/venv/bin/celery \
    -A backend.queue.celery_app worker \
    --loglevel=info \
    --concurrency=4 \
    --max-tasks-per-child=200 \
    --hostname=protheus-worker@%%h

# Graceful shutdown
KillSignal=SIGTERM
TimeoutStopSec=60

Restart=on-failure
RestartSec=5
StartLimitIntervalSec=60
StartLimitBurst=5

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/protheus-reports/data /opt/protheus-reports/reports_output /opt/protheus-reports/logs

[Install]
WantedBy=multi-user.target
```

### 4.3 Scheduler (APScheduler — embarcado no web)

> **Importante:** o APScheduler do Auditor Fiscal **roda dentro do processo
> FastAPI** (chamado em `backend/main.py` lifespan). Não precisa de unit
> separada — sobe junto com `protheus-reports-web.service`.

Se no futuro o projeto migrar para **Celery Beat** (cron centralizado em vez
do APScheduler in-process), use a unit abaixo:

`/etc/systemd/system/protheus-reports-beat.service` *(opcional — só se trocar para Celery Beat)*

```ini
[Unit]
Description=Protheus Reports — Celery Beat (agendador)
After=network.target redis-server.service
Requires=redis-server.service

[Service]
Type=simple
User=protheus
Group=protheus
WorkingDirectory=/opt/protheus-reports/app
EnvironmentFile=/opt/protheus-reports/app/.env
Environment="PYTHONUNBUFFERED=1"
Environment="PATH=/opt/protheus-reports/venv/bin"

ExecStart=/opt/protheus-reports/venv/bin/celery \
    -A backend.queue.celery_app beat \
    --loglevel=info \
    --schedule=/opt/protheus-reports/data/celerybeat-schedule

Restart=on-failure
RestartSec=5

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/protheus-reports/data

[Install]
WantedBy=multi-user.target
```

### 4.4 Ativar e iniciar os serviços

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now protheus-reports-web.service
sudo systemctl enable --now protheus-reports-worker.service
# Beat só se aplicou a unit opcional:
# sudo systemctl enable --now protheus-reports-beat.service

# Validar
sudo systemctl status protheus-reports-web --no-pager
sudo systemctl status protheus-reports-worker --no-pager
journalctl -u protheus-reports-web -n 50 --no-pager
journalctl -u protheus-reports-worker -n 50 --no-pager
```

---

## 5. Nginx — Proxy Reverso

`/etc/nginx/sites-available/protheus-reports`

```nginx
upstream protheus_app {
    server 127.0.0.1:8000;
    keepalive 32;
}

server {
    listen 80 default_server;
    listen [::]:80 default_server;

    server_name auditor.fertimaxi.lan _;

    # Limite generoso — exports XLSX podem passar de 50 MB
    client_max_body_size 64M;

    # Logs separados
    access_log /var/log/nginx/protheus-reports.access.log;
    error_log  /var/log/nginx/protheus-reports.error.log warn;

    # === Arquivos estáticos (frontend) ===
    # Serve direto pelo Nginx — mais rápido que via uvicorn.
    location /static/ {
        alias /opt/protheus-reports/app/frontend/;
        access_log off;
        expires 7d;
        add_header Cache-Control "public, no-transform";
    }

    # === Branding (logo dinâmico) — passa pelo FastAPI ===
    location /api/branding/logo {
        proxy_pass http://protheus_app;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        expires 1h;
    }

    # === Aplicação (FastAPI) ===
    location / {
        proxy_pass http://protheus_app;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade           $http_upgrade;
        proxy_set_header Connection        "upgrade";

        # Auditoria pode demorar — não usar timeout curto demais
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        proxy_connect_timeout 30s;

        # Buffering off para SSE/streaming de progresso de jobs
        proxy_buffering off;
    }

    # Health-check direto (sem auth)
    location = /health {
        proxy_pass http://protheus_app/health;
        access_log off;
    }
}
```

### Ativar o site

```bash
sudo ln -sf /etc/nginx/sites-available/protheus-reports /etc/nginx/sites-enabled/protheus-reports
sudo rm -f /etc/nginx/sites-enabled/default     # remove o catch-all
sudo nginx -t                                    # valida sintaxe
sudo systemctl reload nginx
```

### TLS (HTTPS) — quando houver CA interna

```bash
# Coloque os certs em /etc/ssl/protheus-reports/
# Substitua o bloco "listen 80" por:
#   listen 443 ssl http2;
#   ssl_certificate     /etc/ssl/protheus-reports/fullchain.pem;
#   ssl_certificate_key /etc/ssl/protheus-reports/privkey.pem;
# E adicione um server redirect:
#   server { listen 80; server_name auditor.fertimaxi.lan;
#            return 301 https://$host$request_uri; }
```

---

## 6. Script de Atualização — `update.sh`

`/opt/protheus-reports/update.sh`

```bash
#!/usr/bin/env bash
# update.sh — Atualização do Protheus Reports em produção.
# Uso:    sudo /opt/protheus-reports/update.sh
# Pré-req: o código novo já foi enviado para /opt/protheus-reports/app/
#          (via FTP/WinSCP/git pull); este script só reinstala deps e reinicia.

set -euo pipefail

APP_DIR="/opt/protheus-reports/app"
VENV_DIR="/opt/protheus-reports/venv"
DATA_DIR="/opt/protheus-reports/data"
SERVICES=(protheus-reports-web protheus-reports-worker)
# Adicione protheus-reports-beat se estiver usando Celery Beat:
# SERVICES+=(protheus-reports-beat)

log() { printf "\e[32m[update]\e[0m %s\n" "$*"; }
err() { printf "\e[31m[update][ERRO]\e[0m %s\n" "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || err "Rode como root (sudo)."

log "1/6 — Backup rápido do app.db (snapshot pré-deploy)"
mkdir -p /var/backups/protheus-reports
STAMP=$(date +%Y%m%d_%H%M%S)
if [[ -f "$DATA_DIR/app.db" ]]; then
    cp -a "$DATA_DIR/app.db" "/var/backups/protheus-reports/app.db.$STAMP"
    log "    -> /var/backups/protheus-reports/app.db.$STAMP"
fi

log "2/6 — Reinstalando dependências Python"
sudo -u protheus "$VENV_DIR/bin/pip" install --upgrade pip wheel -q
sudo -u protheus "$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt" -q

log "3/6 — Limpando cache Python (.pyc / __pycache__)"
find "$APP_DIR" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
find "$APP_DIR" -type f -name '*.pyc' -delete 2>/dev/null || true

log "4/6 — Reinicializando serviços"
systemctl daemon-reload
for svc in "${SERVICES[@]}"; do
    log "    -> restart $svc"
    systemctl restart "$svc"
done

log "5/6 — Aguardando 5s e validando status"
sleep 5
for svc in "${SERVICES[@]}"; do
    if systemctl is-active --quiet "$svc"; then
        log "    OK $svc"
    else
        err "$svc não subiu — veja: journalctl -u $svc -n 80"
    fi
done

log "6/6 — Smoke test no /health"
if curl -fsS -o /dev/null http://127.0.0.1:8000/health; then
    log "    OK aplicação respondendo na 8000"
else
    err "Aplicação não responde — veja journalctl -u protheus-reports-web -n 80"
fi

log "Deploy concluído. Versão atual:"
sudo -u protheus "$VENV_DIR/bin/python" -c \
    "from backend.version import VERSION, BUILD_DATE, CODENAME; \
     print(f'  -> v{VERSION} build {BUILD_DATE} ({CODENAME})')"
```

Tornar executável:

```bash
sudo chmod +x /opt/protheus-reports/update.sh
sudo chown root:protheus /opt/protheus-reports/update.sh
```

### Fluxo padrão de atualização (gestor de infra)

1. Recebe `.zip` da nova versão da equipe de desenvolvimento.
2. Envia via WinSCP/SFTP para `/tmp/protheus-update.zip`.
3. SSH na VM e executa:

```bash
cd /tmp && unzip -o protheus-update.zip -d /tmp/protheus-new
sudo rsync -a --delete \
    --exclude='/.env' --exclude='/data' --exclude='/reports_output' \
    /tmp/protheus-new/ /opt/protheus-reports/app/
sudo chown -R protheus:protheus /opt/protheus-reports/app
sudo /opt/protheus-reports/update.sh
```

> O `rsync --delete` remove arquivos que sumiram do release. Os `--exclude`
> preservam `.env`, banco e relatórios.

---

## 7. Backup, Logs e Monitoramento

### 7.1 Backup diário do `data/`

`/etc/cron.daily/backup-protheus-reports` (executar `chmod +x`):

```bash
#!/bin/bash
set -euo pipefail
STAMP=$(date +%Y%m%d_%H%M)
DEST=/var/backups/protheus-reports
mkdir -p "$DEST"
tar -czf "$DEST/data_$STAMP.tar.gz" -C /opt/protheus-reports data
# Mantém últimos 30 dias
find "$DEST" -name 'data_*.tar.gz' -mtime +30 -delete
```

**Conteúdo crítico do backup:**
- `data/app.db` — SQLite com usuários, agendamentos, anomalias, AppSetting
- `data/branding/` — logo customizado
- `data/secrets/` — eventuais arquivos sensíveis
- `app/.env` — backup **separado** (cofre), contém `MASTER_KEY` e `JWT_SECRET`

### 7.2 Logs

Todos os logs vão para o **journald** automaticamente. Para ver:

```bash
# Logs em tempo real
sudo journalctl -u protheus-reports-web -f
sudo journalctl -u protheus-reports-worker -f

# Logs do dia
sudo journalctl -u protheus-reports-web --since today

# Filtrar erros
sudo journalctl -u protheus-reports-web -p err --since "1 hour ago"
```

Logs do Nginx:

```bash
sudo tail -f /var/log/nginx/protheus-reports.access.log
sudo tail -f /var/log/nginx/protheus-reports.error.log
```

### 7.3 Monitoramento (smoke test cron-friendly)

```bash
# /etc/cron.d/protheus-healthcheck
*/5 * * * * root curl -fsS -o /dev/null http://127.0.0.1:8000/health || \
    systemctl restart protheus-reports-web
```

---

## 8. Validação final (checklist de aceite)

Depois de F1–F10, execute na VM:

```bash
# 1) Serviços ativos
sudo systemctl is-active protheus-reports-web protheus-reports-worker redis-server nginx
# Esperado: 4x "active"

# 2) ODBC + SQL Server alcançável
sqlcmd -S $SQL_HOST -U $SQL_USER -P $SQL_PWD -Q "SELECT TOP 1 name FROM sys.databases" -C

# 3) Aplicação respondendo via Nginx
curl -fsS http://127.0.0.1/health
# Esperado: {"status":"ok",...}

# 4) Versão correta
curl -fsS http://127.0.0.1/health | python3 -m json.tool

# 5) Smoke do Celery — enfileira tarefa simples
sudo -u protheus /opt/protheus-reports/venv/bin/celery \
    -A backend.queue.celery_app inspect ping
# Esperado: {"protheus-worker@HOSTNAME": {"ok": "pong"}}

# 6) Acesso pelo navegador interno
# http://auditor.fertimaxi.lan/static/pages/login.html
```

---

## 9. Operações comuns (cheat sheet do gestor de infra)

| Tarefa | Comando |
|---|---|
| Reiniciar web | `sudo systemctl restart protheus-reports-web` |
| Reiniciar worker | `sudo systemctl restart protheus-reports-worker` |
| Reiniciar tudo | `sudo systemctl restart protheus-reports-web protheus-reports-worker` |
| Recarregar Nginx | `sudo systemctl reload nginx` |
| Ver logs web | `journalctl -u protheus-reports-web -n 200 --no-pager` |
| Ver logs worker | `journalctl -u protheus-reports-worker -n 200 --no-pager` |
| Status geral | `systemctl status protheus-reports-*` |
| Deploy de nova versão | `sudo /opt/protheus-reports/update.sh` |
| Backup manual | `sudo /etc/cron.daily/backup-protheus-reports` |
| Apagar fila de jobs travados (CUIDADO) | `redis-cli FLUSHDB` |
| Espaço em disco | `du -sh /opt/protheus-reports/* \| sort -h` |

---

## 10. Riscos & Mitigações

| Risco | Mitigação |
|---|---|
| `pyodbc` não acha driver | Validar `odbcinst -q -d` antes de subir worker; `msodbcsql18` instalado em **F3** |
| `MASTER_KEY` perdida | Cópia em cofre Fertimaxi + backup do `.env` separado do `data/` |
| Redis down → jobs travam | `Requires=redis-server.service` no systemd força inicialização; `*/5 * * * *` healthcheck |
| Auditoria pesada trava worker | `--max-tasks-per-child=200` recicla worker; concurrency = vCPU |
| Disco cheio com XLSX antigos | Job de limpeza diária em `cron.daily` (apaga `reports_output/*` com >30 dias) |
| SQL Server fica indisponível | App expõe `ERR-DB-001` no painel; APScheduler segue rodando |
| Atualização quebra .env | `update.sh` faz backup automático antes de `pip install` |
| VM reinicia | Todos os serviços têm `WantedBy=multi-user.target` — sobem no boot |
| Senhas/tokens vazam em log | `journald` filtra; revisar antes de enviar suporte |

---

## Definition of Done — Sprint 16

- [ ] VM Proxmox provisionada com sizing recomendado (4 vCPU / 8 GB / 80 GB SSD)
- [ ] Firewall Datacenter Proxmox configurado com as portas dos tópicos 1.2
- [ ] Ubuntu 24.04 + Python 3.12 + Redis + Nginx + msodbcsql18 instalados
- [ ] Usuário `protheus` criado, estrutura `/opt/protheus-reports/` montada
- [ ] `.env` preenchido com `JWT_SECRET` + `MASTER_KEY` GERADOS (não os defaults)
- [ ] 2 units systemd ativas: `protheus-reports-web` e `protheus-reports-worker`
- [ ] Nginx servindo http://auditor.fertimaxi.lan
- [ ] Smoke test (`curl /health`, `celery inspect ping`) passando
- [ ] `update.sh` em `/opt/protheus-reports/` testado
- [ ] Backup diário cron + retenção 30d ativo
- [ ] Senhas/MASTER_KEY arquivadas no cofre da Fertimaxi
- [ ] Equipe de desenvolvimento confirmou login + 1 auditoria real OK

**Pronto para produção.**

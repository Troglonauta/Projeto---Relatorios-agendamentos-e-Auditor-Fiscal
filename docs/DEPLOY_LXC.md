# Implantação em LXC (Proxmox · Bare Metal) — Protheus Reports

Guia completo de produção rodando direto em **Linux Container do Proxmox** com
Debian 12 (bookworm) ou Ubuntu 22.04+. Sem Docker. Sem virtualização aninhada.

**Resultado esperado:** uma única IP/porta 80 servindo a aplicação via Nginx,
com `uvicorn` + `celery worker` + `redis` rodando nativos como serviços
gerenciados pelo `systemd` — uso de RAM esperado: **~250 MB total** em idle.

---

## 0. Visão geral arquitetural

```
       Internet / LAN
              │
              ▼
   ┌──────────────────────┐
   │   Nginx (porta 80)   │  ← TLS pode ser adicionado depois (Let's Encrypt)
   └────────┬─────────────┘
            │ proxy_pass
            ▼
   ┌──────────────────────┐         ┌──────────────────────────┐
   │  uvicorn :8000       │ ◄─────► │  /opt/protheus-reports/  │
   │  (systemd service)   │         │  ├─ data/app.db          │
   └─────┬────────────────┘         │  ├─ data/branding/       │
         │                          │  ├─ reports_output/      │
         │ Celery API               │  └─ .env                 │
         ▼                          └──────────────────────────┘
   ┌──────────────────────┐
   │  redis :6379         │
   │  (apt nativo)        │
   └─────┬────────────────┘
         ▲
         │ broker
   ┌─────┴────────────────┐
   │  celery worker       │  ← processa relatórios pesados + auditor fiscal
   │  (systemd service)   │
   └──────────────────────┘
```

Tudo roda sob o usuário **`protheus`** (não-root). Os caminhos canônicos:

| Caminho | Conteúdo |
|---|---|
| `/opt/protheus-reports/` | Código (clone do git) |
| `/opt/protheus-reports/.venv/` | Virtualenv Python |
| `/opt/protheus-reports/.env` | Segredos (chmod 640) |
| `/opt/protheus-reports/data/` | SQLite `app.db` + `branding/` + `secrets/` |
| `/opt/protheus-reports/reports_output/` | Arquivos gerados pelo worker |
| `/var/log/protheus-reports/` | Logs estruturados (gerados pelo journalctl + Nginx) |

---

## 1. Pré-requisitos do host Proxmox

No nó Proxmox (`pve`), criar o container LXC:

```bash
# Recursos minimos recomendados (testado para uso medio Fertimaxi)
pct create 200 local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst \
    --hostname protheus-reports \
    --memory 4096 --swap 1024 \
    --cores 4 \
    --rootfs local-lvm:32 \
    --net0 name=eth0,bridge=vmbr0,ip=dhcp \
    --features nesting=1 \
    --onboot 1 \
    --unprivileged 1 \
    --password
```

**Por que `nesting=1`?** Permite que o `systemd` interno do container
gerencie cgroups corretamente (necessário para `Restart=always`).

Suba o container e entre:

```bash
pct start 200
pct enter 200
```

A partir daqui, todos os comandos rodam **dentro do LXC**.

---

## 2. Pré-requisitos do LXC (Debian 12)

### 2.1 — Atualização + pacotes essenciais

```bash
apt update && apt upgrade -y

apt install -y \
    python3 python3-pip python3-venv \
    git curl wget gnupg2 ca-certificates apt-transport-https \
    build-essential unixodbc unixodbc-dev \
    nginx redis-server \
    sudo nano htop logrotate
```

**Por que cada um:**
- `python3.11+` → backend FastAPI.
- `unixodbc-dev` → headers necessários para `pyodbc` compilar.
- `redis-server` → broker do Celery (10 MB de RAM em idle).
- `nginx` → proxy reverso na porta 80.
- `build-essential` → wheels que não têm binário pré-compilado.

### 2.2 — ODBC Driver 17 for SQL Server (Microsoft)

**Passo crítico** — sem isso o `pyodbc` falha com `IM002`.

```bash
# 1. Adiciona repo da Microsoft
curl -sSL https://packages.microsoft.com/keys/microsoft.asc \
    | gpg --dearmor -o /usr/share/keyrings/microsoft.gpg

echo "deb [arch=amd64,arm64 signed-by=/usr/share/keyrings/microsoft.gpg] \
https://packages.microsoft.com/debian/12/prod bookworm main" \
    > /etc/apt/sources.list.d/microsoft.list

# 2. Instala
apt update
ACCEPT_EULA=Y apt install -y msodbcsql17

# 3. Confere
odbcinst -j                              # mostra paths das libs ODBC
odbcinst -q -d                           # lista drivers — deve aparecer "ODBC Driver 17 for SQL Server"
```

> **Ubuntu 22.04**: troque `debian/12/prod bookworm` por `ubuntu/22.04/prod jammy`.
> Demais comandos idênticos.

### 2.3 — Usuário de serviço (não-root)

```bash
adduser --system --group --home /opt/protheus-reports --shell /bin/bash protheus
```

Isso cria o usuário `protheus`, grupo `protheus` e o diretório `/opt/protheus-reports/`
já dono dele.

---

## 3. Setup inicial do projeto

```bash
# 1. Clone o repositorio
sudo -u protheus -H bash << 'EOF'
cd /opt/protheus-reports
git clone https://github.com/<sua-org>/protheus-reports.git .

# 2. Virtualenv + dependencias
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip wheel
pip install -r requirements.txt

# 3. Diretorios de runtime (com permissoes)
mkdir -p data reports_output data/branding data/secrets

# 4. Copia template do .env e edita
cp .env.example .env
EOF

# 5. Edita o .env como root (porque chmod 640 vai vir)
nano /opt/protheus-reports/.env
```

**Conteúdo crítico do `.env`** (preencher antes de subir o serviço):

```ini
# Aplicacao
APP_NAME=Protheus Reports — Fertimaxi
APP_ENV=production
APP_HOST=0.0.0.0
APP_PORT=8000
APP_TIMEZONE=America/Sao_Paulo

# Seguranca (gere uma chave forte!)
# python -c "import secrets; print(secrets.token_urlsafe(64))"
JWT_SECRET=__GERE_UMA_CHAVE_LONGA_E_UNICA__
JWT_EXPIRE_MINUTES=480
SESSION_IDLE_MINUTES=20

# Master key Fernet (PRESERVE com backup — perder = perder TODAS as credenciais)
# Gere uma com: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Se deixar em branco, o app gera no primeiro boot e grava aqui.
MASTER_KEY=

# Banco da aplicacao (SQLite local, ok ate 50 usuarios concorrentes)
DATABASE_URL=sqlite:////opt/protheus-reports/data/app.db

# Protheus (vira do Wizard, mas pode prepopular aqui)
PROTHEUS_DB_URL=mssql+pyodbc://...

# Celery — Redis nativo do LXC
QUEUE_BROKER_URL=redis://localhost:6379/0
QUEUE_RESULT_BACKEND=redis://localhost:6379/1

# Scheduler
SCHEDULER_INTERVAL_MINUTES=60
REPORTS_OUTPUT_DIR=/opt/protheus-reports/reports_output
```

Permissões do `.env` (contém segredos):

```bash
chown protheus:protheus /opt/protheus-reports/.env
chmod 640 /opt/protheus-reports/.env
```

**Permissões da pasta `data/`** — crítico para web E worker poderem escrever:

```bash
chown -R protheus:protheus /opt/protheus-reports
chmod -R u+rwX,g+rX,o-rwx /opt/protheus-reports/data /opt/protheus-reports/reports_output
```

### Seed do admin inicial (opcional — pode usar o Wizard)

Se preferir já criar o admin via CLI:

```bash
sudo -u protheus -H bash -c \
  "cd /opt/protheus-reports && .venv/bin/python -m scripts.seed_admin"
```

---

## 4. Redis nativo

O Redis já vem instalado e habilitado pelo `apt`. Confirme:

```bash
systemctl status redis-server
redis-cli ping       # deve responder PONG
```

Por padrão escuta apenas em `127.0.0.1:6379` — o que é o que queremos (Celery
e Redis no mesmo container).

**Hardening opcional** (impede que apps fora do container o usem):
edite `/etc/redis/redis.conf` e confirme:
```
bind 127.0.0.1 ::1
protected-mode yes
```

---

## 5. Serviços systemd

### 5.1 — Web (uvicorn)

Crie `/etc/systemd/system/protheus-reports-web.service`:

```ini
[Unit]
Description=Protheus Reports — FastAPI Web (uvicorn)
After=network-online.target redis-server.service
Wants=network-online.target redis-server.service

[Service]
Type=simple
User=protheus
Group=protheus
WorkingDirectory=/opt/protheus-reports
EnvironmentFile=/opt/protheus-reports/.env
ExecStart=/opt/protheus-reports/.venv/bin/uvicorn backend.main:app \
    --host 127.0.0.1 \
    --port 8000 \
    --workers 2 \
    --proxy-headers \
    --forwarded-allow-ips=127.0.0.1
Restart=always
RestartSec=2
SuccessExitStatus=3
TimeoutStopSec=15

# Hardening — confina o servico ao maximo
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/protheus-reports/data /opt/protheus-reports/reports_output /opt/protheus-reports/.env
# Reservamos .env como ReadWrite porque o crypto.py pode regravar a MASTER_KEY
# se ela estiver ausente no primeiro boot.

# Limites razoaveis
LimitNOFILE=4096
StandardOutput=journal
StandardError=journal
SyslogIdentifier=protheus-web

[Install]
WantedBy=multi-user.target
```

**Pontos críticos:**
- `--host 127.0.0.1` (não `0.0.0.0`) — só o Nginx fala com o uvicorn.
- `--proxy-headers` + `--forwarded-allow-ips=127.0.0.1` — confia no `X-Forwarded-For` do Nginx.
- `Restart=always` + `SuccessExitStatus=3` — `os._exit(3)` do botão "Reiniciar" no Admin é tratado como sucesso e re-spawn.
- `ReadWritePaths` lista todos os caminhos que precisam de escrita; o resto fica read-only.

### 5.2 — Worker (Celery)

Crie `/etc/systemd/system/protheus-reports-worker.service`:

```ini
[Unit]
Description=Protheus Reports — Celery Worker
After=network-online.target redis-server.service protheus-reports-web.service
Wants=network-online.target redis-server.service

[Service]
Type=simple
User=protheus
Group=protheus
WorkingDirectory=/opt/protheus-reports
EnvironmentFile=/opt/protheus-reports/.env
ExecStart=/opt/protheus-reports/.venv/bin/celery \
    -A backend.queue.celery_app worker \
    --loglevel=info \
    --concurrency=2 \
    --pool=prefork \
    --max-tasks-per-child=50
Restart=always
RestartSec=3
TimeoutStopSec=30
KillSignal=SIGTERM

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/protheus-reports/data /opt/protheus-reports/reports_output

# Worker pode consumir mais memoria (pandas)
LimitNOFILE=4096
StandardOutput=journal
StandardError=journal
SyslogIdentifier=protheus-worker

[Install]
WantedBy=multi-user.target
```

**Notas:**
- `--pool=prefork --concurrency=2` é o padrão Linux (Windows usaria `--pool=solo`).
- `--max-tasks-per-child=50` recicla o worker a cada 50 tasks (mitiga vazamento de memória do pandas em jobs longos).
- `KillSignal=SIGTERM` + `TimeoutStopSec=30` dá ao Celery 30s para terminar a task atual antes de matar.

### 5.3 — Habilitar e iniciar

```bash
systemctl daemon-reload
systemctl enable --now protheus-reports-web.service
systemctl enable --now protheus-reports-worker.service

# Confere
systemctl status protheus-reports-web.service
systemctl status protheus-reports-worker.service

# Logs ao vivo
journalctl -u protheus-reports-web.service -f
journalctl -u protheus-reports-worker.service -f
```

---

## 6. Nginx — proxy reverso

Crie `/etc/nginx/sites-available/protheus-reports`:

```nginx
# Upstream — uvicorn local
upstream protheus_app {
    server 127.0.0.1:8000;
    keepalive 32;
}

# Tamanho maximo de upload (logo do Wizard, etc) — ajuste se precisar
client_max_body_size 25M;

server {
    listen 80;
    listen [::]:80;
    server_name protheus.fertimaxi.local;   # troque pelo seu dominio/IP

    # Logs separados
    access_log /var/log/nginx/protheus-reports.access.log;
    error_log  /var/log/nginx/protheus-reports.error.log;

    # Health check direto (nao bate no uvicorn em loop)
    location = /health {
        proxy_pass http://protheus_app;
        access_log off;
    }

    # Estaticos com cache (servir o frontend gera muitos requests)
    location /static/ {
        proxy_pass http://protheus_app;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # Cache de 1 dia (HTML/JS/CSS podem mudar via deploy)
        proxy_cache_valid 200 1d;
        expires 1d;
        add_header Cache-Control "public";
    }

    # Downloads grandes (relatorios xlsx/csv via fila)
    location /api/reports/jobs/ {
        proxy_pass http://protheus_app;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # Permite stream de arquivos sem timeout
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }

    # Todo o resto
    location / {
        proxy_pass http://protheus_app;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";
        proxy_read_timeout 60s;
        proxy_send_timeout 60s;
    }
}
```

Habilite e recarregue:

```bash
ln -s /etc/nginx/sites-available/protheus-reports /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default     # remove o default do Debian
nginx -t                                   # valida sintaxe
systemctl reload nginx
```

Acesse pelo browser: `http://<IP-do-LXC>/` → será redirecionado para `setup.html`
no primeiro acesso (ou login se o Wizard já foi finalizado).

---

## 7. Firewall (opcional, recomendado)

Se o LXC não estiver atrás de outro firewall:

```bash
apt install -y ufw

ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp                           # SSH
ufw allow 80/tcp                           # HTTP
# ufw allow 443/tcp                        # HTTPS (quando configurar TLS)
ufw enable
ufw status verbose
```

---

## 8. HTTPS com Let's Encrypt (opcional)

Quando o cliente apontar um DNS público para o LXC:

```bash
apt install -y certbot python3-certbot-nginx
certbot --nginx -d protheus.fertimaxi.com.br

# Renovacao automatica via cron ja vem habilitada (/etc/cron.d/certbot)
certbot renew --dry-run                    # confirma que renova OK
```

O certbot ajusta o bloco `server` do Nginx para você (adiciona `listen 443 ssl`
e redirect 80→443).

---

## 9. Logs e logrotate

Logs do `uvicorn` e `celery` vão para o **journald** (via `StandardOutput=journal`).
Consulte com:

```bash
# Ao vivo
journalctl -u protheus-reports-web.service -f
journalctl -u protheus-reports-worker.service -f

# Ultimas 100 linhas
journalctl -u protheus-reports-web.service -n 100 --no-pager

# Filtro por nivel (so erros)
journalctl -u protheus-reports-worker.service -p err --since "1 hour ago"
```

Nginx tem logs em `/var/log/nginx/protheus-reports.access.log` e
`.error.log`. O Debian já vem com `logrotate` configurado para `/etc/logrotate.d/nginx`.

---

## 10. Atualizações (deploy de nova versão)

```bash
sudo -u protheus -H bash << 'EOF'
cd /opt/protheus-reports
git fetch && git pull
source .venv/bin/activate
pip install -r requirements.txt
EOF

# Aplica (graceful restart — o web aguenta requests em voo)
systemctl restart protheus-reports-web.service
systemctl restart protheus-reports-worker.service

# Confere
systemctl status protheus-reports-web.service
curl -s http://localhost/health | jq
```

> **Antes do `git pull`** confira `git status` — o `.env` não está versionado, então deve
> aparecer como "untracked". Se aparecer modificação em arquivo do código, alguém editou em produção (ruim).

---

## 11. Backup obrigatório

Faça backup **conjunto** dos 3 itens (perder a `MASTER_KEY` = perder todas as credenciais cifradas):

```bash
# 1. SQLite + branding
tar -czf /backup/protheus-reports-$(date +%Y%m%d).tar.gz \
    /opt/protheus-reports/data \
    /opt/protheus-reports/.env

# 2. (opcional) reports_output — geralmente nao precisa, sao arquivos derivados
```

Coloque num cron diário:

```bash
cat > /etc/cron.daily/protheus-backup << 'EOF'
#!/bin/bash
set -e
DEST=/backup/protheus-reports-$(date +%Y%m%d).tar.gz
mkdir -p /backup
tar -czf $DEST /opt/protheus-reports/data /opt/protheus-reports/.env
# Mantem so os ultimos 30 dias
find /backup -name 'protheus-reports-*.tar.gz' -mtime +30 -delete
EOF
chmod +x /etc/cron.daily/protheus-backup
```

---

## 12. Troubleshooting comum

| Sintoma | Diagnóstico | Solução |
|---|---|---|
| `502 Bad Gateway` no Nginx | uvicorn não está rodando | `systemctl status protheus-reports-web.service` |
| `IM002 Data source name not found` | Driver ODBC ausente | `odbcinst -q -d` deve listar "ODBC Driver 17 for SQL Server"; reinstale com `apt install --reinstall msodbcsql17` |
| Worker conecta e cai logo | Permissão na `data/` | `chown -R protheus:protheus /opt/protheus-reports/data` |
| `ConnectionError: redis://localhost:6379` | Redis caiu | `systemctl restart redis-server` |
| Botão "Reiniciar" no Admin não retorna | systemd não está vendo o exit code 3 | confira `SuccessExitStatus=3` no service file |
| Login fica em loop | `JWT_SECRET` diferente entre boots | gere uma chave fixa e grave no `.env` (não deixe vazio) |
| Workers consomem 1GB+ RAM | Pandas leak em jobs longos | já mitigado por `--max-tasks-per-child=50`, mas pode reduzir para 20 |
| HTTPS quebra após o certbot | falta `--proxy-headers` no uvicorn | já está no service file; conferir que `X-Forwarded-Proto` chega |

### Quick check (one-liner)

```bash
curl -s http://localhost/health | python3 -m json.tool
# Esperado:
# {
#   "status": "ok",
#   "app": "Protheus Reports — Fertimaxi",
#   "version": "1.4.0",
#   "build_date": "2026-05-14",
#   "phase": "Fase 4",
#   "setup_complete": true,
#   "timezone": "America/Sao_Paulo"
# }
```

---

## 13. Apêndice — requisitos de hardware mínimos

| Recurso | Mínimo | Recomendado | Para 20+ usuários simultâneos |
|---|---|---|---|
| vCPU | 2 | **4** | 8 |
| RAM | 2 GB | **4 GB** | 8 GB |
| Disco | 16 GB | **32 GB** | 64 GB |
| Rede | 100 Mbps | 1 Gbps | 1 Gbps |

**Footprint medido em idle:**
- uvicorn (2 workers): ~120 MB
- celery worker: ~80 MB
- redis: ~10 MB
- nginx: ~5 MB
- **Total**: ~215 MB de RAM em idle.

---

## 14. Checklist final de implantação

- [ ] Container LXC criado com `nesting=1`
- [ ] `apt update && apt upgrade -y`
- [ ] Pacotes do sistema instalados (Python 3.11+, nginx, redis-server, unixodbc-dev)
- [ ] **ODBC Driver 17** instalado (`odbcinst -q -d` mostra-o)
- [ ] Usuário `protheus` criado
- [ ] Código clonado em `/opt/protheus-reports`
- [ ] `.venv` criado e `requirements.txt` instalado
- [ ] `.env` configurado com `JWT_SECRET` forte
- [ ] `chown protheus:protheus` em todo o projeto
- [ ] `data/` e `reports_output/` graváveis pelo `protheus`
- [ ] `/etc/systemd/system/protheus-reports-web.service` criado
- [ ] `/etc/systemd/system/protheus-reports-worker.service` criado
- [ ] `systemctl daemon-reload && enable --now` em ambos
- [ ] `redis-cli ping` retorna `PONG`
- [ ] `/etc/nginx/sites-available/protheus-reports` criado e habilitado
- [ ] `nginx -t` ok, `systemctl reload nginx`
- [ ] `curl http://localhost/health` retorna 200
- [ ] Firewall UFW habilitado (opcional)
- [ ] Backup diário no cron
- [ ] HTTPS Let's Encrypt (quando DNS público estiver pronto)

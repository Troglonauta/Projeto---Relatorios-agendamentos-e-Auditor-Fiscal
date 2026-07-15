"""Celery app — broker e backend resolvidos dinamicamente.

Resolucao do broker (em ordem):
1. `QUEUE_BROKER_URL` em settings_store ou env. Se setado, usa.
2. Tenta ping no Redis em `localhost:6379` (timeout 1s). Se OK, usa Redis.
3. Fallback dev: `sqla+sqlite:///data/celery_broker.db` (Kombu suporta SQLAlchemy
   como transport — nao requer Redis instalado em Windows dev).

Para subir o worker em dev (Windows, sem Redis):
    python -m celery -A backend.queue.celery_app worker -l info --pool=solo

Em producao LXC (Proxmox), o systemd injeta `QUEUE_BROKER_URL=redis://localhost:6379/0`
via `EnvironmentFile=/opt/protheus-reports/.env` e o fallback nao e' acionado.

Trade-offs do broker SQLite:
- Polling-based (lento): nao adequado para alto throughput.
- Suficiente para 1 worker dev gerando 1-2 relatorios por minuto.
- Cancelamento e progresso continuam funcionando porque usamos a tabela `jobs`
  (SQLite proprio do app) para esses metadados.
"""
from __future__ import annotations

import logging
import os
import socket
from pathlib import Path

from celery import Celery

logger = logging.getLogger(__name__)


# ---- Paths para os DBs do broker SQLite (modo dev) -------------------------
# Usamos arquivos separados do app.db para nao competir por lock e nao corromper
# o WAL do banco principal.
ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DEV_BROKER_URL = f"sqla+sqlite:///{(DATA_DIR / 'celery_broker.db').as_posix()}"
DEV_RESULT_URL = f"db+sqlite:///{(DATA_DIR / 'celery_results.db').as_posix()}"


def _redis_alive(url: str, timeout: float = 1.0) -> bool:
    """Tenta um socket TCP no host:port da URL. Nao importa o protocolo Redis
    de verdade — so se a porta esta aberta.
    """
    try:
        # extrai host:port simples (redis://host:port/db)
        without_scheme = url.split("://", 1)[-1]
        hostport = without_scheme.split("/", 1)[0]
        if "@" in hostport:
            hostport = hostport.split("@", 1)[-1]
        host, _, port = hostport.partition(":")
        port = int(port or "6379")
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _resolve_broker() -> tuple[str, str, str]:
    """Retorna (broker_url, result_backend_url, mode_descricao)."""
    # 1) Env explicito (systemd EnvironmentFile em prod LXC, ou .env)
    env_broker = os.environ.get("QUEUE_BROKER_URL")
    env_result = os.environ.get("QUEUE_RESULT_BACKEND")
    if env_broker:
        return env_broker, env_result or env_broker.replace("/0", "/1", 1), "env"

    # 2) AppSetting (Wizard/Admin gravou)
    try:
        from ..security import settings_store
        cfg_broker = settings_store.get_setting("QUEUE_BROKER_URL")
        cfg_result = settings_store.get_setting("QUEUE_RESULT_BACKEND")
        if cfg_broker:
            return cfg_broker, cfg_result or cfg_broker.replace("/0", "/1", 1), "settings_store"
    except Exception:
        # Settings_store pode nao estar pronto quando o worker importa este modulo
        pass

    # 3) Auto-detect Redis local
    local_redis = "redis://localhost:6379/0"
    if _redis_alive(local_redis):
        return local_redis, "redis://localhost:6379/1", "redis-local-auto"

    # 4) Fallback dev: SQLite (nao requer servico extra)
    return DEV_BROKER_URL, DEV_RESULT_URL, "sqlite-dev"


_broker_url, _result_url, _mode = _resolve_broker()

if _mode == "sqlite-dev":
    logger.warning(
        "[Celery] Broker em modo DEV SQLite (%s). Sem Redis disponivel. "
        "Adequado para dev local, NAO use em producao.", _broker_url,
    )
else:
    logger.info("[Celery] Broker resolvido (%s): %s", _mode, _broker_url)


celery_app = Celery(
    "protheus_reports",
    broker=_broker_url,
    backend=_result_url,
    include=[
        "backend.queue.tasks.report_task",
        "backend.queue.tasks.fiscal_task",
        "backend.queue.tasks.scheduled_run_task",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="America/Sao_Paulo",
    enable_utc=False,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_time_limit=3600,
    task_soft_time_limit=3500,
    worker_max_tasks_per_child=50,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)


# Fork-safety (Celery prefork + SQLite/SQLAlchemy): cada processo FILHO precisa de
# conexoes PROPRIAS. Se reusar uma conexao herdada do pai, os PRAGMAs por-conexao
# (busy_timeout=30s, WAL) podem nao valer -> "database is locked" instantaneo em
# vez de aguardar o lock. engine.dispose() no init de cada filho forca conexoes
# novas, que disparam o evento `connect` e aplicam os PRAGMAs.
from celery.signals import worker_process_init as _worker_process_init


@_worker_process_init.connect
def _dispose_db_engine_on_fork(**_):
    try:
        from ..database import engine
        engine.dispose()
        logger.info("[Celery] engine SQLite descartado no fork do worker (PRAGMAs reaplicados)")
    except Exception:
        logger.exception("[Celery] Falha ao descartar o engine no fork do worker")

# Tweaks especificos por transport. O SQLAlchemy transport NAO aceita
# `polling_interval` em `transport_options` (passa direto para `create_engine`).
# Por isso deixamos defaults aqui — o polling default e' ~1s, suficiente
# para dev (so 1 worker, sem alta concorrencia).

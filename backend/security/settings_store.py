"""CRUD para a tabela `app_settings` com cache e criptografia transparente.

Pontos chave:
- `get_setting(key, default=None)` resolve nesta ordem:
    1. Cache em memoria.
    2. SQLite (`AppSetting.encrypted_value` -> Fernet decrypt, ou `plain_value`).
    3. Variavel de ambiente (`os.environ`) — usado durante o boot, antes da
       migracao, e como fallback para chaves nao migradas (ex.: MASTER_KEY).
    4. Default.
- `set_setting(key, value, is_secret, scope, ...)` grava em SQLite, invalida o
  cache e (se a sessao for fornecida) faz commit dentro da transacao do caller.
- `invalidate_cache()` chama na rota `/api/admin/reload-config`.
- `migrate_env_to_settings()` roda uma unica vez no lifespan: se ja foi migrado
  (presenca de `migrated_from_env_at`), nao faz nada.

Nao depende de `backend.config` (config agora consome este modulo).
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import AppSetting
from .crypto import CryptoError, decrypt, encrypt

logger = logging.getLogger(__name__)

_cache: dict[str, Any] = {}
_cache_lock = threading.Lock()

SENTINEL = object()


# ---- Catalogo de chaves migradas do .env -----------------------------------
# (chave_env, scope, is_secret, descricao curta)
ENV_KEYS_TO_MIGRATE: list[tuple[str, str, bool, str]] = [
    ("APP_NAME",                 "app",      False, "Nome do app exibido na UI"),
    ("APP_TIMEZONE",             "app",      False, "Fuso horario padrao"),
    ("SESSION_IDLE_MINUTES",     "app",      False, "Minutos de inatividade ate auto-logout"),
    ("PROTHEUS_TABLE_SUFFIX",    "app",      False, "Sufixo do nome fisico de tabela Protheus"),
    ("JWT_EXPIRE_MINUTES",       "app",      False, "TTL do token JWT em minutos"),
    ("SCHEDULER_INTERVAL_MINUTES","app",     False, "Janela do worker de agendamentos"),
    ("PROTHEUS_DB_URL",          "db",       True,  "URL de conexao SQL Server do Protheus"),
    ("SMTP_HOST",                "smtp",     False, "Servidor SMTP"),
    ("SMTP_PORT",                "smtp",     False, "Porta SMTP"),
    ("SMTP_USER",                "smtp",     False, "Usuario SMTP de disparo"),
    ("SMTP_PASSWORD",            "smtp",     True,  "Senha SMTP"),
    ("SMTP_FROM",                "smtp",     False, "Endereco remetente"),
    ("SMTP_USE_TLS",             "smtp",     False, "Usar STARTTLS"),
    # Defaults Fase 3
    ("QUEUE_BROKER_URL",         "queue",    True,  "URL do Redis (broker Celery)"),
    ("QUEUE_RESULT_BACKEND",     "queue",    True,  "URL do Redis (result backend)"),
    ("MAX_REPORT_ROWS",          "queue",    False, "Limite de linhas por job"),
]


# ---- Cache helpers ---------------------------------------------------------

def invalidate_cache(key: Optional[str] = None) -> None:
    """Limpa todo o cache, ou apenas a chave informada."""
    with _cache_lock:
        if key is None:
            _cache.clear()
        else:
            _cache.pop(key, None)


def _cache_get(key: str):
    with _cache_lock:
        return _cache.get(key, SENTINEL)


def _cache_set(key: str, value: Any) -> None:
    with _cache_lock:
        _cache[key] = value


# ---- Leitura ---------------------------------------------------------------

def _resolve_from_db(db: Session, key: str) -> Any:
    """Le da tabela, descripta se necessario, devolve string ou None."""
    row: Optional[AppSetting] = db.query(AppSetting).filter(AppSetting.key == key).first()
    if not row:
        return SENTINEL
    if row.is_secret:
        if not row.encrypted_value:
            return None
        try:
            return decrypt(row.encrypted_value)
        except CryptoError as exc:
            logger.error("Falha ao descriptografar %s: %s", key, exc)
            return None
    return row.plain_value


def get_setting(key: str, default: Any = None, db: Optional[Session] = None) -> Any:
    """Resolve setting com ordem: cache -> DB -> env -> default."""
    hit = _cache_get(key)
    if hit is not SENTINEL:
        return hit

    own_session = False
    if db is None:
        db = SessionLocal()
        own_session = True
    try:
        value = _resolve_from_db(db, key)
        if value is SENTINEL or value is None:
            value = os.environ.get(key, default)
    finally:
        if own_session:
            db.close()

    _cache_set(key, value)
    return value


def list_settings(scope: Optional[str] = None, db: Optional[Session] = None) -> list[dict]:
    """Lista settings (sem revelar segredos). Util para UI do Wizard."""
    own_session = False
    if db is None:
        db = SessionLocal()
        own_session = True
    try:
        q = db.query(AppSetting)
        if scope:
            q = q.filter(AppSetting.scope == scope)
        return [
            {
                "key": r.key,
                "scope": r.scope,
                "is_secret": r.is_secret,
                "has_value": bool(r.encrypted_value or r.plain_value),
                "value": None if r.is_secret else r.plain_value,
                "description": r.description,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in q.order_by(AppSetting.scope, AppSetting.key).all()
        ]
    finally:
        if own_session:
            db.close()


# ---- Escrita ---------------------------------------------------------------

def set_setting(
    key: str,
    value: Any,
    *,
    is_secret: bool = False,
    scope: str = "app",
    description: Optional[str] = None,
    updated_by_id: Optional[int] = None,
    db: Optional[Session] = None,
    commit: bool = True,
) -> AppSetting:
    """Cria/atualiza setting. Invalida cache. Se `db` for fornecido, NAO commita
    a sessao (caller controla a transacao); senao usa uma sessao propria.
    """
    own_session = False
    if db is None:
        db = SessionLocal()
        own_session = True
    try:
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        if row is None:
            row = AppSetting(key=key)
            db.add(row)
        row.scope = scope
        row.is_secret = is_secret
        if description is not None:
            row.description = description
        row.updated_at = datetime.utcnow()
        if updated_by_id is not None:
            row.updated_by_id = updated_by_id

        text = "" if value is None else str(value)
        if is_secret:
            row.encrypted_value = encrypt(text) if text else None
            row.plain_value = None
        else:
            row.plain_value = text
            row.encrypted_value = None

        if commit:
            db.commit()
            db.refresh(row)
        invalidate_cache(key)
        return row
    finally:
        if own_session:
            db.close()


def delete_setting(key: str, db: Optional[Session] = None) -> bool:
    own_session = False
    if db is None:
        db = SessionLocal()
        own_session = True
    try:
        row = db.query(AppSetting).filter(AppSetting.key == key).first()
        if not row:
            return False
        db.delete(row)
        db.commit()
        invalidate_cache(key)
        return True
    finally:
        if own_session:
            db.close()


# ---- Migracao .env -> SQLite ----------------------------------------------

def migrate_env_to_settings() -> bool:
    """Migra UMA VEZ valores do .env para a tabela. Retorna True se migrou.

    Idempotente: se ja existe a flag `migrated_from_env_at`, nao faz nada.
    Nao apaga o `.env` (rollback). Mantem `JWT_SECRET` e `MASTER_KEY` no env
    (sao usados antes do banco estar pronto).
    """
    db = SessionLocal()
    try:
        flag = db.query(AppSetting).filter(AppSetting.key == "migrated_from_env_at").first()
        if flag is not None:
            return False

        migrated = 0
        for env_key, scope, is_secret, desc in ENV_KEYS_TO_MIGRATE:
            val = os.environ.get(env_key)
            if val is None or val == "":
                continue
            # Nao sobrescreve se ja foi setado manualmente
            existing = db.query(AppSetting).filter(AppSetting.key == env_key).first()
            if existing is not None:
                continue
            set_setting(
                env_key, val, is_secret=is_secret, scope=scope,
                description=desc, db=db, commit=False,
            )
            migrated += 1

        # Marca como migrado mesmo se nao migrou nada (primeiro boot limpo)
        set_setting(
            "migrated_from_env_at", datetime.utcnow().isoformat(),
            scope="app", is_secret=False,
            description="Timestamp da migracao automatica do .env",
            db=db, commit=False,
        )
        db.commit()
        logger.info("Migracao .env -> AppSetting: %d chaves migradas.", migrated)
        return True
    finally:
        db.close()


def setup_complete() -> bool:
    """True quando o Wizard ja foi finalizado e o app deve liberar todas as rotas."""
    return str(get_setting("setup_complete", "false")).lower() == "true"

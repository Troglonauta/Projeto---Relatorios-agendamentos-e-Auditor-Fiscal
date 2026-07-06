"""Fernet wrapper para encriptar valores sensiveis em SQLite.

Regras:
- MASTER_KEY (32 bytes, base64 url-safe) e' lida do `.env` na variavel `MASTER_KEY`.
- Se ausente, geramos uma nova com `Fernet.generate_key()` e gravamos no `.env`
  (write-once, com lock de arquivo). Logamos WARNING grande para o operador
  fazer o backup imediatamente — perder a chave = perder todas credenciais.
- A funcao `ensure_master_key()` deve ser chamada UMA VEZ no lifespan, antes
  de qualquer `encrypt/decrypt`.

Importante:
- Esse modulo NAO deve depender de `backend.config` (evitar circular: config
  agora consulta `settings_store` que consulta este modulo).
- Toda excecao de descriptografia (token corrompido, chave errada) sobe como
  `CryptoError` — quem chamar decide se loga e segue com fallback ou aborta.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

# .env fica na raiz do projeto (mesmo nivel de requirements.txt).
ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE = ROOT / ".env"

_fernet: Optional[Fernet] = None
_fernet_lock = threading.Lock()


class CryptoError(RuntimeError):
    """Erro de criptografia (token invalido, chave inexistente, etc)."""


# ---- Inicializacao da master key --------------------------------------------

def _read_env_key() -> Optional[str]:
    """Le `MASTER_KEY=...` do `.env` se existir. NAO depende de pydantic
    (precisa funcionar antes do Settings carregar).
    """
    if not ENV_FILE.exists():
        return None
    for raw in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if line.startswith("MASTER_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _append_master_key_to_env(key: str) -> None:
    """Acrescenta `MASTER_KEY=...` ao .env. Cria o arquivo se nao existir.

    Idempotente: se ja existe a linha MASTER_KEY=, NAO sobrescreve (assume
    que a chave foi setada por outra rota — provavelmente concorrencia).
    """
    if ENV_FILE.exists():
        text = ENV_FILE.read_text(encoding="utf-8", errors="ignore")
        if any(l.strip().startswith("MASTER_KEY=") for l in text.splitlines()):
            return
        sep = "" if text.endswith("\n") or not text else "\n"
        ENV_FILE.write_text(
            f"{text}{sep}\n# Gerada automaticamente — guarde junto com app.db (perder = perder credenciais)\nMASTER_KEY={key}\n",
            encoding="utf-8",
        )
    else:
        ENV_FILE.write_text(
            f"# Arquivo gerado automaticamente no primeiro boot\nMASTER_KEY={key}\n",
            encoding="utf-8",
        )


def ensure_master_key() -> bytes:
    """Garante que existe uma master key carregada. Retorna a chave em bytes.

    Chame UMA VEZ no lifespan do FastAPI antes de qualquer `encrypt/decrypt`.
    """
    global _fernet
    with _fernet_lock:
        if _fernet is not None:
            return _fernet._signing_key + _fernet._encryption_key  # type: ignore[attr-defined]

        # 1) Tenta env (LXC systemd EnvironmentFile / .env)
        key = os.environ.get("MASTER_KEY") or _read_env_key()

        if not key:
            key = Fernet.generate_key().decode("ascii")
            _append_master_key_to_env(key)
            os.environ["MASTER_KEY"] = key
            logger.warning(
                "\n"
                "============================================================\n"
                "  MASTER_KEY gerada automaticamente e gravada em .env.\n"
                "  Faça BACKUP IMEDIATO de .env + data/app.db juntos.\n"
                "  Perder a chave = perder todas as credenciais criptografadas.\n"
                "============================================================\n"
            )
        else:
            os.environ["MASTER_KEY"] = key

        try:
            _fernet = Fernet(key.encode("ascii") if isinstance(key, str) else key)
        except Exception as exc:
            raise CryptoError(
                f"MASTER_KEY invalida (deve ser 32 bytes base64 url-safe): {exc}"
            ) from exc

        logger.info("Fernet master key carregada (len=%d).", len(key))
        return key.encode("ascii") if isinstance(key, str) else key


def get_fernet() -> Fernet:
    """Retorna o Fernet inicializado. Chama `ensure_master_key` se necessario."""
    if _fernet is None:
        ensure_master_key()
    assert _fernet is not None
    return _fernet


# ---- API publica -------------------------------------------------------------

def encrypt(plaintext: str) -> str:
    """Encripta string em token Fernet (base64 url-safe). Aceita string vazia."""
    if plaintext is None:
        return ""
    token = get_fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt(token: str) -> str:
    """Descripta token. Lanca `CryptoError` se invalido ou chave incompativel."""
    if not token:
        return ""
    try:
        return get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise CryptoError(
            "Token Fernet invalido — MASTER_KEY mudou ou dado corrompido"
        ) from exc
    except Exception as exc:
        raise CryptoError(f"Falha ao descriptografar: {exc}") from exc

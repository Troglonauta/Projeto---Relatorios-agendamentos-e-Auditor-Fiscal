"""CLI para rotacao segura da MASTER_KEY (Fernet).

Cenarios de uso:
1. Suspeita de vazamento da chave atual — rotaciona para uma nova.
2. Quer trocar a chave de teste pela chave de producao.

O que faz:
1. Le `MASTER_KEY` atual do .env (ou aceita via --old-key).
2. Conecta no SQLite (`data/app.db`), busca todas linhas com `is_secret=True`.
3. Descripta cada `encrypted_value` com a chave ANTIGA.
4. Encripta com a chave NOVA.
5. UPDATE em transacao unica — ou tudo OK, ou rollback completo.
6. Faz backup do .env e grava a chave nova.
7. Salva log da operacao em audit_logs.

Uso:
    python -m backend.cli.rotate_master_key                       # gera chave nova
    python -m backend.cli.rotate_master_key --new-key "abc..."    # usa chave fornecida
    python -m backend.cli.rotate_master_key --dry-run             # so simula
    python -m backend.cli.rotate_master_key --old-key "..." --new-key "..."

ATENCAO:
- Faca BACKUP de `.env` e `data/app.db` ANTES de rodar.
- Apos rotacao, restart obrigatorio (a aplicacao em memoria tem a chave antiga).
- Se algo falhar no meio, a transacao rollback — nao deixa dados em estado misto.
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE = ROOT / ".env"


def _read_env_key() -> str | None:
    if not ENV_FILE.exists():
        return None
    for line in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.strip().startswith("MASTER_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _write_env_key(new_key: str) -> Path:
    """Sobrescreve MASTER_KEY no .env, criando backup do arquivo antigo."""
    backup = ENV_FILE.with_suffix(f".env.bak-{datetime.utcnow():%Y%m%dT%H%M%S}")
    shutil.copy2(ENV_FILE, backup)
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    rewritten = []
    found = False
    for line in lines:
        if line.strip().startswith("MASTER_KEY="):
            rewritten.append(f"MASTER_KEY={new_key}")
            found = True
        else:
            rewritten.append(line)
    if not found:
        rewritten.append(f"MASTER_KEY={new_key}")
    ENV_FILE.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    return backup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rotaciona a MASTER_KEY (Fernet) com seguranca.")
    parser.add_argument("--old-key", help="Chave atual. Se omitida, le do .env (MASTER_KEY=)")
    parser.add_argument("--new-key", help="Chave nova. Se omitida, gera uma com Fernet.generate_key()")
    parser.add_argument("--dry-run", action="store_true", help="Simula sem persistir")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # ---- Chave antiga -----------------------------------------------------
    old_key = args.old_key or _read_env_key()
    if not old_key:
        logger.error("MASTER_KEY atual nao localizada — passe --old-key ou garanta .env com a linha MASTER_KEY=...")
        return 2
    try:
        old_fernet = Fernet(old_key.encode("ascii"))
    except Exception as exc:
        logger.error("MASTER_KEY atual e' invalida: %s", exc)
        return 2

    # ---- Chave nova -------------------------------------------------------
    new_key = args.new_key or Fernet.generate_key().decode("ascii")
    try:
        new_fernet = Fernet(new_key.encode("ascii"))
    except Exception as exc:
        logger.error("MASTER_KEY nova e' invalida: %s", exc)
        return 2

    if old_key == new_key:
        logger.error("Chave nova e identica a atual — nada para fazer.")
        return 1

    # ---- Conecta no app DB (com a chave antiga ainda em vigor) ------------
    # Garante que o backend.security.crypto vai usar a chave OLD para descripta.
    os.environ["MASTER_KEY"] = old_key
    from ..database import SessionLocal
    from ..models import AppSetting, AuditLog
    from ..security import crypto

    # Forca re-init do Fernet do backend
    crypto._fernet = None
    crypto.ensure_master_key()

    db = SessionLocal()
    try:
        secrets = db.query(AppSetting).filter(AppSetting.is_secret.is_(True)).all()
        if not secrets:
            logger.info("Nenhum AppSetting com is_secret=True — nada para reencriptar.")
        else:
            logger.info("Reencriptando %d setting(s) secret(s)...", len(secrets))

        # Decripta com a chave OLD e encripta com a NOVA
        rotated = 0
        failures = []
        for row in secrets:
            if not row.encrypted_value:
                continue
            try:
                plaintext = old_fernet.decrypt(row.encrypted_value.encode("ascii")).decode("utf-8")
            except InvalidToken as exc:
                failures.append((row.key, "old-key nao descripta este token"))
                continue
            try:
                row.encrypted_value = new_fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
                rotated += 1
                logger.info("  [OK] %s", row.key)
            except Exception as exc:
                failures.append((row.key, f"falha ao reencriptar: {exc}"))

        if failures:
            logger.error("FALHAS encontradas:")
            for k, msg in failures:
                logger.error("  - %s: %s", k, msg)
            logger.error("Abortando — rollback.")
            db.rollback()
            return 3

        if args.dry_run:
            logger.info("--dry-run: nada foi persistido. %d setting(s) seriam rotacionados.", rotated)
            db.rollback()
            return 0

        # Persiste antes de mexer no .env (se .env falhar, app continua com chave antiga)
        db.commit()

        # Atualiza .env (com backup automatico)
        backup = _write_env_key(new_key)
        logger.info("MASTER_KEY rotacionada. Backup do .env antigo: %s", backup)

        # Audit log
        db.add(AuditLog(
            username="(cli)",
            action="cli.rotate_master_key",
            detail=f"settings_rotated={rotated} backup={backup.name}",
            success=True,
        ))
        db.commit()

        logger.info("")
        logger.info("Rotacao concluida com sucesso.")
        logger.info(">>> REINICIE A APLICACAO AGORA — a chave em memoria ainda e' a antiga. <<<")
        if not args.new_key:
            logger.info("Chave nova gerada: %s", new_key)
            logger.info("Guarde com seguranca (esta gravada no .env, mas anote tambem).")
        return 0
    except Exception:
        logger.exception("Erro inesperado — rollback.")
        db.rollback()
        return 4
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

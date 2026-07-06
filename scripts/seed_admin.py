"""Cria o usuário Administrador inicial se ele ainda não existir.

Execução:
    python -m scripts.seed_admin

As credenciais saem do .env (SEED_ADMIN_*). Em produção, troque a senha
do admin no primeiro login (o flag `must_change_password` é True por
padrão para qualquer usuário criado por outra pessoa).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Garante que o backend esteja no sys.path quando rodado de qualquer pasta.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.auth import hash_password
from backend.config import get_settings
from backend.database import Base, SessionLocal, engine
from backend.models import Role, User, UserActionPermission

settings = get_settings()


def main() -> int:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == settings.SEED_ADMIN_USERNAME).first()
        if existing:
            print(f"[seed] Admin '{settings.SEED_ADMIN_USERNAME}' já existe — nada a fazer.")
            return 0

        admin = User(
            username=settings.SEED_ADMIN_USERNAME,
            email=settings.SEED_ADMIN_EMAIL,
            full_name="Administrador",
            hashed_password=hash_password(settings.SEED_ADMIN_PASSWORD),
            role=Role.ADMIN,
            is_active=True,
            # O admin seedado NÃO precisa trocar senha — quem semeou já controla a credencial.
            must_change_password=False,
        )
        db.add(admin)
        db.flush()
        for action in ("view", "export", "schedule"):
            db.add(UserActionPermission(user_id=admin.id, action=action))
        db.commit()
        print(f"[seed] Admin '{admin.username}' criado. Faça login com a senha do .env.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

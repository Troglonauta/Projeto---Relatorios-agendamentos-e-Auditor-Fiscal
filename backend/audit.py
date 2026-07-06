"""Helper único para gravar linhas em `audit_logs`.

Centralizar aqui evita repetir o mesmo padrão em cada router.
"""
from typing import Optional
from sqlalchemy.orm import Session

from .models import AuditLog, User


def log(
    db: Session,
    *,
    action: str,
    user: Optional[User] = None,
    detail: Optional[str] = None,
    ip: Optional[str] = None,
    success: bool = True,
) -> AuditLog:
    entry = AuditLog(
        user_id=user.id if user else None,
        username=user.username if user else None,
        action=action,
        detail=detail,
        ip_address=ip,
        success=success,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry

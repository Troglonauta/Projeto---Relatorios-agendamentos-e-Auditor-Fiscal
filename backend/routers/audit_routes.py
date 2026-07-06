"""Visualização da trilha de auditoria — apenas admins."""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_admin
from ..models import AuditLog
from ..schemas import AuditLogOut

router = APIRouter(prefix="/api/audit", tags=["audit"], dependencies=[Depends(require_admin)])


@router.get("", response_model=List[AuditLogOut])
def list_logs(
    db: Session = Depends(get_db),
    username: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    success: Optional[bool] = Query(None),
    since: Optional[datetime] = Query(None),
    until: Optional[datetime] = Query(None),
    limit: int = Query(200, le=2000),
    offset: int = Query(0),
):
    q = db.query(AuditLog)
    if username:
        q = q.filter(AuditLog.username == username)
    if action:
        q = q.filter(AuditLog.action.like(f"%{action}%"))
    if success is not None:
        q = q.filter(AuditLog.success.is_(success))
    if since:
        q = q.filter(AuditLog.timestamp >= since)
    if until:
        q = q.filter(AuditLog.timestamp <= until)
    return q.order_by(AuditLog.timestamp.desc()).offset(offset).limit(limit).all()


@router.get("/summary")
def summary(db: Session = Depends(get_db)):
    """Métricas agregadas para alimentar o dashboard (Chart.js)."""
    from sqlalchemy import func

    by_action = (
        db.query(AuditLog.action, func.count(AuditLog.id))
        .group_by(AuditLog.action).order_by(func.count(AuditLog.id).desc()).limit(10).all()
    )
    by_user = (
        db.query(AuditLog.username, func.count(AuditLog.id))
        .filter(AuditLog.username.isnot(None))
        .group_by(AuditLog.username).order_by(func.count(AuditLog.id).desc()).limit(10).all()
    )
    fail_count = db.query(func.count(AuditLog.id)).filter(AuditLog.success.is_(False)).scalar() or 0
    total = db.query(func.count(AuditLog.id)).scalar() or 0
    return {
        "total": total,
        "failures": fail_count,
        "by_action": [{"action": a, "count": c} for a, c in by_action],
        "by_user": [{"user": u, "count": c} for u, c in by_user],
    }

"""CRUD de usuários — exclusivo para Administradores."""
import logging
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import audit
from ..auth import generate_temp_password, hash_password
from ..database import get_db
from ..deps import get_client_ip, require_admin
from ..email_service import send_welcome
from ..models import Profile, User, UserActionPermission, UserProfile, UserTablePermission
from ..schemas import UserCreate, UserOut, UserUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users", tags=["users"], dependencies=[Depends(require_admin)])


def _to_out(u: User) -> UserOut:
    return UserOut(
        id=u.id, username=u.username, email=u.email, full_name=u.full_name,
        role=u.role, is_active=u.is_active, must_change_password=u.must_change_password,
        last_login_at=u.last_login_at,
        created_at=u.created_at, created_by=u.created_by,
        deleted_at=u.deleted_at,
        allowed_tables=[p.table_name for p in u.table_permissions],
        allowed_actions=[p.action for p in u.action_permissions],
        allowed_profiles=sorted({up.profile.code for up in (u.profile_links or []) if up.profile}),
    )


def _apply_profiles(db: Session, user: User, codes: list[str]) -> None:
    """Substitui o conjunto de perfis vinculados ao usuario.

    Aliases desconhecidos sao filtrados silenciosamente (UI ja oferece
    apenas perfis validos). Vazio = remove todos os vinculos.
    """
    requested = {c.upper().strip() for c in codes if c and c.strip()}
    profiles = db.query(Profile).filter(Profile.code.in_(requested)).all() if requested else []
    # Apaga vinculos antigos
    db.query(UserProfile).filter(UserProfile.user_id == user.id).delete(synchronize_session=False)
    for p in profiles:
        db.add(UserProfile(user_id=user.id, profile_id=p.id))


@router.get("", response_model=List[UserOut])
def list_users(db: Session = Depends(get_db), include_deleted: bool = False):
    q = db.query(User)
    if not include_deleted:
        q = q.filter(User.deleted_at.is_(None))
    return [_to_out(u) for u in q.order_by(User.username).all()]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    existing = db.query(User).filter(
        (User.username == payload.username) | (User.email == payload.email)
    ).first()
    if existing:
        # v2.22 — diferencia conflito com usuario INATIVO (soft-deleted): o
        # username/email continua reservado. Orienta a reativar em vez de travar
        # sem explicacao.
        if existing.deleted_at is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Usuário/e-mail pertence a um cadastro INATIVO ({existing.username}). "
                f"Use 'Mostrar inativos' e clique em Reativar, ou cadastre com outro "
                f"usuário/e-mail.",
            )
        raise HTTPException(status.HTTP_409_CONFLICT, "Username ou e-mail já cadastrado")

    user = User(
        username=payload.username,
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        role=payload.role,
        # Toda primeira senha cadastrada por admin deve ser trocada no 1º login.
        must_change_password=True,
        # v2.21 — registra quem criou (visivel na tela de Usuarios + trilha).
        created_by=getattr(admin, "username", None),
    )
    db.add(user)
    db.flush()  # obtém user.id

    for t in payload.allowed_tables:
        db.add(UserTablePermission(user_id=user.id, table_name=t.upper()))
    for a in payload.allowed_actions:
        db.add(UserActionPermission(user_id=user.id, action=a))
    # Perfis (Fase 4)
    if payload.allowed_profiles:
        _apply_profiles(db, user, payload.allowed_profiles)
    db.commit()
    db.refresh(user)

    # Sprint 5 — boas-vindas por e-mail (best-effort).
    # Como o admin escolheu a senha no cadastro (nao geramos temp), enviamos a
    # senha original como "inicial" — usuario sera obrigado a trocar no 1o login.
    welcome_sent = False
    try:
        send_welcome(
            to=str(payload.email), username=payload.username,
            email=str(payload.email), full_name=payload.full_name,
            role=payload.role, temp_password=payload.password,
        )
        welcome_sent = True
    except Exception as exc:
        logger.warning("Falha ao enviar boas-vindas para %s: %s",
                       payload.email, exc)

    audit.log(db, action="user.create", user=admin, ip=get_client_ip(request),
              detail=f"created={user.username} role={user.role} "
                     f"por={getattr(admin, 'username', '?')} "
                     f"profiles={payload.allowed_profiles} welcome={welcome_sent}")
    return _to_out(user)


@router.put("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    user = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuário não encontrado")

    if payload.email is not None:
        user.email = payload.email
    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.role is not None:
        user.role = payload.role
    if payload.is_active is not None:
        user.is_active = payload.is_active

    # Bulk-delete (emite o DELETE imediatamente) antes de re-inserir — evita
    # "UNIQUE constraint failed" que ocorria com collection.clear() (o flush
    # reordenava INSERT antes do DELETE quando o novo conjunto tinha overlap).
    if payload.allowed_tables is not None:
        db.query(UserTablePermission).filter(
            UserTablePermission.user_id == user.id
        ).delete(synchronize_session=False)
        seen_t = set()
        for t in payload.allowed_tables:
            tu = t.upper()
            if tu in seen_t:
                continue
            seen_t.add(tu)
            db.add(UserTablePermission(user_id=user.id, table_name=tu))
    if payload.allowed_actions is not None:
        db.query(UserActionPermission).filter(
            UserActionPermission.user_id == user.id
        ).delete(synchronize_session=False)
        for a in dict.fromkeys(payload.allowed_actions):  # dedupe preservando ordem
            db.add(UserActionPermission(user_id=user.id, action=a))
    # Perfis (Fase 4) — None = mantem; lista vazia = remove todos
    if payload.allowed_profiles is not None:
        _apply_profiles(db, user, payload.allowed_profiles)

    db.commit()
    db.refresh(user)
    audit.log(db, action="user.update", user=admin, ip=get_client_ip(request),
              detail=f"updated={user.username}")
    return _to_out(user)


@router.delete("/{user_id}")
def soft_delete_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    """Soft delete — preserva auditoria histórica."""
    user = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuário não encontrado")
    if user.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Você não pode inativar a si mesmo")
    user.is_active = False
    user.deleted_at = datetime.utcnow()
    db.commit()
    audit.log(db, action="user.soft_delete", user=admin, ip=get_client_ip(request),
              detail=f"deleted={user.username}")
    return {"detail": "Usuário inativado"}


@router.post("/{user_id}/reactivate")
def reactivate_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    """v2.22 — Reativa um usuario inativado (soft-deleted).

    Limpa `deleted_at` e marca `is_active=True`, liberando novamente o
    username/e-mail (que continuavam reservados enquanto inativo). Preserva o
    cadastro original (perfis/permissoes/historico)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuário não encontrado")
    if user.deleted_at is None and user.is_active:
        return {"detail": "Usuário já está ativo"}
    user.deleted_at = None
    user.is_active = True
    db.commit()
    audit.log(db, action="user.reactivate", user=admin, ip=get_client_ip(request),
              detail=f"reactivated={user.username} por={getattr(admin, 'username', '?')}")
    return {"detail": "Usuário reativado"}


@router.delete("/{user_id}/purge")
def hard_delete_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    """v2.23 — EXCLUSAO DEFINITIVA (hard delete) do usuario.

    Diferente do soft-delete (Inativar), remove FISICAMENTE o cadastro e libera
    username/e-mail. Como o SQLite roda com FK enforcement OFF, as referencias
    sao tratadas a mao: dependencias proprias do usuario sao apagadas
    (permissoes/perfis/sessoes/consultas salvas/tokens/agendamentos) e os
    registros historicos sao preservados desvinculando o autor (auditoria/jobs/
    settings/anomalias/revisoes). A TRILHA de auditoria (audit_logs) e mantida —
    so o vinculo user_id e' anulado; o username textual permanece.
    """
    from ..models import (
        ActiveSession, AppSetting, AuditLog, FiscalAnomaly, FiscalDocumentReview,
        FiscalFieldDecision, Job, PasswordResetToken, SavedQuery, ScheduledReport,
    )
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuário não encontrado")
    if user.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Você não pode excluir a si mesmo")

    username = user.username
    uid = user.id
    # 1) Apaga dependencias que pertencem ao usuario.
    db.query(UserTablePermission).filter(UserTablePermission.user_id == uid).delete(synchronize_session=False)
    db.query(UserActionPermission).filter(UserActionPermission.user_id == uid).delete(synchronize_session=False)
    db.query(UserProfile).filter(UserProfile.user_id == uid).delete(synchronize_session=False)
    db.query(ActiveSession).filter(ActiveSession.user_id == uid).delete(synchronize_session=False)
    db.query(SavedQuery).filter(SavedQuery.user_id == uid).delete(synchronize_session=False)
    db.query(PasswordResetToken).filter(PasswordResetToken.user_id == uid).delete(synchronize_session=False)
    db.query(ScheduledReport).filter(ScheduledReport.owner_id == uid).delete(synchronize_session=False)
    # 2) Preserva registros historicos: apenas desvincula o autor.
    db.query(AuditLog).filter(AuditLog.user_id == uid).update({AuditLog.user_id: None}, synchronize_session=False)
    db.query(AppSetting).filter(AppSetting.updated_by_id == uid).update({AppSetting.updated_by_id: None}, synchronize_session=False)
    db.query(Job).filter(Job.owner_id == uid).update({Job.owner_id: None}, synchronize_session=False)
    db.query(FiscalAnomaly).filter(FiscalAnomaly.acknowledged_by_id == uid).update({FiscalAnomaly.acknowledged_by_id: None}, synchronize_session=False)
    db.query(FiscalDocumentReview).filter(FiscalDocumentReview.reviewed_by_id == uid).update({FiscalDocumentReview.reviewed_by_id: None}, synchronize_session=False)
    db.query(FiscalFieldDecision).filter(FiscalFieldDecision.decided_by_id == uid).update({FiscalFieldDecision.decided_by_id: None}, synchronize_session=False)
    # 3) Remove o usuario.
    db.delete(user)
    db.commit()
    audit.log(db, action="user.hard_delete", user=admin, ip=get_client_ip(request),
              detail=f"purged={username} por={getattr(admin, 'username', '?')}")
    return {"detail": f"Usuário {username} excluído definitivamente"}


@router.post("/{user_id}/reset-password")
def admin_reset_password(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    """Admin força reset — gera nova senha temporária e devolve no payload.

    O frontend exibe a senha UMA vez na tela para o admin entregar manualmente
    (útil quando o e-mail do usuário ainda não está validado).
    """
    from ..auth import generate_temp_password

    user = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuário não encontrado")

    temp = generate_temp_password()
    user.hashed_password = hash_password(temp)
    user.must_change_password = True
    db.commit()
    db.refresh(user)
    audit.log(db, action="user.admin_reset_password", user=admin, ip=get_client_ip(request),
              detail=f"target={user.username}")
    return {"user": _to_out(user).model_dump(), "temp_password": temp}

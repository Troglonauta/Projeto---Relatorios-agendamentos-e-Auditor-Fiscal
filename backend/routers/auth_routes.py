"""Login, troca de senha obrigatória, 'esqueci a senha' e logout (Fase 4)."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from .. import audit
from ..auth import (
    create_access_token, decode_access_token, generate_temp_password,
    hash_password, verify_password,
)
from ..database import get_db
from ..deps import get_client_ip, get_current_user, oauth2_scheme
from ..email_service import send_temp_password
from ..models import ActiveSession, PasswordResetToken, User
from ..schemas import (
    ChangePasswordRequest, ForgotPasswordRequest, TokenResponse, UserOut,
)
from ..security import settings_store

DEFAULT_MAX_CONCURRENT = 3
# Uma sessao so conta no limite se teve "sinal de vida" (heartbeat) recente.
# Fechar a aba para de enviar heartbeat -> em ate este intervalo o slot e' liberado.
SESSION_LIVENESS_SECONDS = 150

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_to_out(user: User) -> UserOut:
    return UserOut(
        id=user.id, username=user.username, email=user.email,
        full_name=user.full_name, role=user.role, is_active=user.is_active,
        must_change_password=user.must_change_password,
        last_login_at=user.last_login_at,
        allowed_tables=[p.table_name for p in user.table_permissions],
        allowed_actions=[p.action for p in user.action_permissions],
    )


def _max_concurrent_sessions() -> int:
    """Configuravel via AppSetting; default 3 (contas genericas Fertimaxi)."""
    try:
        v = int(settings_store.get_setting("MAX_CONCURRENT_SESSIONS", DEFAULT_MAX_CONCURRENT))
        return max(1, min(v, 50))
    except Exception:
        return DEFAULT_MAX_CONCURRENT


def _purge_expired_sessions(db: Session, user_id: int) -> None:
    """Remove fisicamente sessoes expiradas ou revogadas do usuario.

    Mantemos a tabela enxuta — fica so o que e' relevante para contagem
    (sessoes ativas, expiraveis no futuro).
    """
    now = datetime.utcnow()
    db.query(ActiveSession).filter(
        ActiveSession.user_id == user_id,
        ((ActiveSession.expires_at < now) | (ActiveSession.revoked_at.isnot(None))),
    ).delete(synchronize_session=False)
    db.commit()


def _live_sessions(db: Session, user_id: int) -> list:
    """Sessoes VIVAS: nao revogadas, nao expiradas e com heartbeat recente.
    Sessoes de abas fechadas (sem heartbeat) saem da contagem em
    `SESSION_LIVENESS_SECONDS`. Ordenadas da mais antiga (por last_seen) p/ a mais nova."""
    now = datetime.utcnow()
    cutoff = now - timedelta(seconds=SESSION_LIVENESS_SECONDS)
    return db.query(ActiveSession).filter(
        ActiveSession.user_id == user_id,
        ActiveSession.expires_at >= now,
        ActiveSession.revoked_at.is_(None),
        ActiveSession.last_seen_at.isnot(None),
        ActiveSession.last_seen_at >= cutoff,
    ).order_by(ActiveSession.last_seen_at.asc()).all()


def _count_active_sessions(db: Session, user_id: int) -> int:
    return len(_live_sessions(db, user_id))


def _enforce_session_limit(db: Session, user_id: int, limit: int) -> int:
    """Garante espaco para +1 sessao: se houver sessoes VIVAS demais, revoga as
    mais antigas (por last_seen) ate caber. Retorna quantas foram revogadas.
    Assim o usuario NUNCA fica bloqueado pelas proprias abas fechadas."""
    live = _live_sessions(db, user_id)
    excess = len(live) - (limit - 1)   # precisa sobrar 1 slot p/ a nova
    revoked = 0
    if excess > 0:
        now = datetime.utcnow()
        for s in live[:excess]:
            s.revoked_at = now
            revoked += 1
        db.commit()
    return revoked


@router.post("/login", response_model=TokenResponse)
def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    ip = get_client_ip(request)
    user = db.query(User).filter(
        User.username == form.username, User.deleted_at.is_(None)
    ).first()

    if not user or not user.is_active or not verify_password(form.password, user.hashed_password):
        audit.log(db, action="auth.login.failed", detail=f"username={form.username}", ip=ip, success=False)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuário ou senha inválidos")

    # === Controle de sessoes concorrentes (Fase 4) =============================
    # Limite ROLLING: conta apenas sessoes VIVAS (com heartbeat recente) e, se
    # estiver no limite, revoga as mais antigas para abrir espaco. O usuario nunca
    # fica bloqueado por abas que ele fechou (a sessao deixa de ter heartbeat).
    _purge_expired_sessions(db, user.id)
    limit = _max_concurrent_sessions()
    revoked = _enforce_session_limit(db, user.id, limit)
    active = _count_active_sessions(db, user.id)

    user.last_login_at = datetime.utcnow()
    db.commit()
    db.refresh(user)
    audit.log(db, action="auth.login.success", user=user, ip=ip,
              detail=f"active_sessions={active + 1}/{limit} revoked_old={revoked}")

    token, jti, expires_at = create_access_token(user.username, extra_claims={"role": user.role})

    # Persiste a sessao ativa (ja com heartbeat inicial em last_seen_at).
    ua = (request.headers.get("user-agent") or "")[:255]
    db.add(ActiveSession(
        jti=jti, user_id=user.id, expires_at=expires_at,
        last_seen_at=datetime.utcnow(),
        ip_address=ip, user_agent=ua,
    ))
    db.commit()

    return TokenResponse(
        access_token=token,
        must_change_password=user.must_change_password,
        user=_user_to_out(user),
    )


@router.post("/logout")
def logout(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Revoga a sessao atual (libera 1 slot de sessoes concorrentes).

    Idempotente — se ja foi revogada, nao reclama. Sempre 200.
    """
    try:
        payload = decode_access_token(token)
        jti = payload.get("jti")
    except Exception:
        jti = None

    if jti:
        s = db.query(ActiveSession).filter(ActiveSession.jti == jti).first()
        if s and s.revoked_at is None:
            s.revoked_at = datetime.utcnow()
            db.commit()
    audit.log(db, action="auth.logout", user=user, ip=get_client_ip(request))
    return {"detail": "Sessão encerrada"}


@router.post("/heartbeat")
def heartbeat(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Sinal de vida da aba aberta — atualiza `last_seen_at` da sessao atual.
    O frontend chama periodicamente; quando a aba fecha, para de chamar e a
    sessao deixa de contar no limite (em ate SESSION_LIVENESS_SECONDS)."""
    try:
        jti = decode_access_token(token).get("jti")
    except Exception:
        jti = None
    if jti:
        s = db.query(ActiveSession).filter(
            ActiveSession.jti == jti, ActiveSession.revoked_at.is_(None)
        ).first()
        if s:
            s.last_seen_at = datetime.utcnow()
            db.commit()
    return {"ok": True}


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not verify_password(payload.current_password, user.hashed_password):
        audit.log(db, action="auth.change_password.failed", user=user, ip=get_client_ip(request), success=False)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Senha atual incorreta")
    if payload.current_password == payload.new_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A nova senha deve ser diferente da atual")

    user.hashed_password = hash_password(payload.new_password)
    user.must_change_password = False
    db.commit()
    audit.log(db, action="auth.change_password.success", user=user, ip=get_client_ip(request))
    return {"detail": "Senha alterada com sucesso"}


@router.post("/forgot-password")
def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Gera senha temporária, atualiza hash do usuário e envia por e-mail.

    Resposta é genérica (200) mesmo se o e-mail não existir, para não vazar
    a base de usuários. A auditoria registra o resultado real.
    """
    ip = get_client_ip(request)
    user = db.query(User).filter(
        User.email == payload.email, User.deleted_at.is_(None), User.is_active.is_(True)
    ).first()

    if not user:
        audit.log(db, action="auth.forgot_password.unknown_email",
                  detail=f"email={payload.email}", ip=ip, success=False)
        return {"detail": "Se o e-mail existir, uma senha temporária foi enviada."}

    temp = generate_temp_password()
    user.hashed_password = hash_password(temp)
    user.must_change_password = True
    db.add(PasswordResetToken(
        user_id=user.id,
        expires_at=datetime.utcnow() + timedelta(hours=2),
    ))
    db.commit()

    try:
        send_temp_password(to=user.email, username=user.username, temp_password=temp)
        audit.log(db, action="auth.forgot_password.sent", user=user, ip=ip)
    except Exception as exc:
        audit.log(db, action="auth.forgot_password.email_failed", user=user,
                  detail=str(exc), ip=ip, success=False)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            "Senha gerada, mas falha no envio. Procure o administrador.")

    return {"detail": "Se o e-mail existir, uma senha temporária foi enviada."}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return _user_to_out(user)

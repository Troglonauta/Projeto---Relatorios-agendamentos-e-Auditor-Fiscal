"""Dependências FastAPI: usuário corrente, RBAC por role, tabela e ação.

Uso típico em router:

    @router.get("/foo", dependencies=[Depends(require_action("export"))])
    def foo(...): ...
"""
from typing import Callable
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.orm import Session

from datetime import datetime

from .auth import decode_access_token
from .database import get_db
from .models import ActiveSession, Role, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    cred_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciais inválidas",
        headers={"WWW-Authenticate": "Bearer"},
    )
    revoked_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Sessão encerrada — faça login novamente",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        username: str = payload.get("sub")
        jti: str | None = payload.get("jti")
        if not username:
            raise cred_exc
    except JWTError:
        raise cred_exc

    user = db.query(User).filter(
        User.username == username, User.is_active.is_(True), User.deleted_at.is_(None)
    ).first()
    if not user:
        raise cred_exc

    # === Fase 4: valida que a sessao ainda esta ativa =========================
    # JWTs sem `jti` sao tokens da Fase 3 (legacy) — aceita sem checar (compat).
    if jti:
        sess = db.query(ActiveSession).filter(ActiveSession.jti == jti).first()
        if sess is None or sess.revoked_at is not None or sess.expires_at < datetime.utcnow():
            raise revoked_exc

    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Acesso restrito a administradores")
    return user


def require_action(action: str) -> Callable:
    """Bloqueia se o usuário não tiver a ação (view/export/schedule)."""
    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role == Role.ADMIN:
            return user  # admin tem todas as ações
        allowed = {p.action for p in user.action_permissions}
        if action not in allowed:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Usuário não autorizado para a ação '{action}'",
            )
        return user
    return _dep


def _aliases_via_profiles(user: User) -> set[str]:
    """Aliases que o usuario acessa via perfis vinculados (Fase 4.A)."""
    allowed: set[str] = set()
    for up in (user.profile_links or []):
        if not up.profile:
            continue
        for tp in up.profile.tables:
            allowed.add(tp.alias.upper())
    return allowed


def assert_table_allowed(user: User, table: str) -> None:
    """Garante que o usuario pode consultar a tabela Protheus solicitada.

    Regra (Fase 4):
    - Admin: sempre passa.
    - Operador: passa se (alias em UserTablePermission direta)
                OU (alias em algum TableProfile dos UserProfile do user).
    """
    if user.role == Role.ADMIN:
        return
    alias_or_table = table.upper()
    direct = {p.table_name.upper() for p in user.table_permissions}
    via_profiles = _aliases_via_profiles(user)
    if alias_or_table in direct or alias_or_table in via_profiles:
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        f"Usuário não autorizado para a tabela '{table}'. "
        f"Solicite ao administrador a vinculação a um perfil que contenha esta tabela.",
    )


def get_client_ip(request: Request) -> str:
    """Pega o IP de cliente respeitando proxy reverso (X-Forwarded-For)."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

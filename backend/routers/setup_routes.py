"""Setup Wizard — Sprint 12 (Auditoria Interna, sem APIs externas).

Endpoints liberados SEM autenticacao quando `setup_complete=False`.
Apos finalizar (`POST /api/setup/finish`), todas as rotas ficam protegidas
e os passos podem ser refeitos apenas por admin (TODO: bloqueio futuro).

Passos (4):
1. Branding (nome do app + cor) — `POST /api/setup/branding`
2. Upload de logo (multipart)  — `POST /api/setup/branding/logo`
3. Banco Protheus              — `POST /api/setup/db` (+ teste `POST /api/setup/test/db`)
4. SMTP                        — `POST /api/setup/smtp` (+ teste `POST /api/setup/test/smtp`)
5. Criar primeiro admin        — `POST /api/setup/admin`
6. Finalizar                   — `POST /api/setup/finish`

Sprint 12: passo "APIs externas" removido — auditoria e' 100% interna ao Protheus.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import aiofiles
from fastapi import APIRouter, File, HTTPException, UploadFile, status
from sqlalchemy import create_engine, text

from .. import audit
from ..auth import hash_password
from ..database import SessionLocal
from ..email_service import send_email_raw
from ..models import User, UserActionPermission
from ..schemas import (
    SetupAdminStep, SetupBrandingStep, SetupDbStep,
    SetupSmtpStep, SetupStateOut, UserOut,
)
from ..security import settings_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/setup", tags=["setup"])

BRANDING_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "branding"
BRANDING_DIR.mkdir(parents=True, exist_ok=True)


# ---- Util: estado do wizard -----------------------------------------------

def _has_admin() -> bool:
    db = SessionLocal()
    try:
        return db.query(User).filter(User.role == "admin", User.deleted_at.is_(None)).first() is not None
    finally:
        db.close()


def _completed_steps() -> list[str]:
    steps = []
    if settings_store.get_setting("APP_NAME"):
        steps.append("branding")
    if settings_store.get_setting("PROTHEUS_DB_URL"):
        steps.append("db")
    if settings_store.get_setting("SMTP_HOST"):
        steps.append("smtp")
    if (BRANDING_DIR / "logo.png").exists():
        steps.append("logo")
    if _has_admin():
        steps.append("admin")
    return steps


def _guard_open():
    """Bloqueia escritas do wizard se setup ja foi finalizado.

    TODO Fase 4: permitir admin alterar mesmo apos finish.
    """
    if settings_store.setup_complete():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Setup ja finalizado. Use a tela de Administracao.",
        )


# ---- Estado ---------------------------------------------------------------

@router.get("/state", response_model=SetupStateOut)
def get_state():
    return SetupStateOut(
        setup_complete=settings_store.setup_complete(),
        completed_steps=_completed_steps(),
        has_admin=_has_admin(),
    )


# ---- Passo 1: branding ----------------------------------------------------

@router.post("/branding")
def save_branding(payload: SetupBrandingStep):
    _guard_open()
    settings_store.set_setting("APP_NAME", payload.app_name, scope="branding",
                               description="Nome do app exibido na UI")
    if payload.primary_color:
        settings_store.set_setting("PRIMARY_COLOR", payload.primary_color,
                                   scope="branding", description="Cor primaria")
    return {"detail": "Branding salvo"}


@router.post("/branding/logo")
async def upload_logo(logo: UploadFile = File(...)):
    _guard_open()
    if logo.content_type not in {"image/png", "image/jpeg", "image/svg+xml"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Formato de logo invalido (use PNG/JPG/SVG)")
    target = BRANDING_DIR / "logo.png"
    async with aiofiles.open(target, "wb") as f:
        while chunk := await logo.read(64 * 1024):
            await f.write(chunk)
    return {"detail": "Logo salvo", "path": "/api/branding/logo"}


# ---- Passo 2: banco Protheus ---------------------------------------------

@router.post("/db")
def save_db(payload: SetupDbStep):
    _guard_open()
    settings_store.set_setting("PROTHEUS_DB_URL", payload.db_url,
                               is_secret=True, scope="db",
                               description="URL ODBC do SQL Server Protheus")
    settings_store.set_setting("PROTHEUS_POOL_SIZE", payload.pool_size, scope="db",
                               description="Tamanho do pool de conexoes")
    settings_store.set_setting("PROTHEUS_MAX_OVERFLOW", payload.max_overflow, scope="db",
                               description="Overflow maximo do pool")
    # Reseta engine para aplicar os novos valores imediatamente
    from ..protheus_api import reset_engine
    reset_engine()
    return {"detail": "Banco salvo"}


@router.post("/test/db")
def test_db(payload: SetupDbStep):
    """Tenta SELECT 1 com a URL fornecida sem persistir nada."""
    try:
        engine = create_engine(payload.db_url, pool_pre_ping=True, future=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1")).scalar()
        engine.dispose()
        return {"ok": True, "detail": "Conexao bem-sucedida"}
    except Exception as exc:
        logger.exception("Teste de DB falhou")
        return {"ok": False, "detail": str(exc)}


# ---- Passo 3: SMTP --------------------------------------------------------

@router.post("/smtp")
def save_smtp(payload: SetupSmtpStep):
    _guard_open()
    settings_store.set_setting("SMTP_HOST", payload.host, scope="smtp",
                               description="Servidor SMTP")
    settings_store.set_setting("SMTP_PORT", payload.port, scope="smtp")
    settings_store.set_setting("SMTP_USER", payload.user, scope="smtp")
    settings_store.set_setting("SMTP_PASSWORD", payload.password, is_secret=True,
                               scope="smtp", description="Senha SMTP")
    settings_store.set_setting("SMTP_FROM", str(payload.sender), scope="smtp")
    settings_store.set_setting("SMTP_USE_TLS", str(payload.use_tls).lower(), scope="smtp")
    return {"detail": "SMTP salvo"}


@router.post("/test/smtp")
def test_smtp(payload: SetupSmtpStep):
    """Tenta enviar um e-mail de teste sem persistir."""
    try:
        send_email_raw(
            host=payload.host, port=payload.port, user=payload.user,
            password=payload.password, use_tls=payload.use_tls,
            sender=str(payload.sender), to=[str(payload.sender)],
            subject="[Teste] Protheus Reports — SMTP",
            body="Este e o e-mail de teste do Setup Wizard. Se voce recebeu, esta tudo OK.",
        )
        return {"ok": True, "detail": "E-mail enviado para o proprio remetente"}
    except Exception as exc:
        logger.exception("Teste de SMTP falhou")
        return {"ok": False, "detail": str(exc)}


# ---- Passo 4: primeiro admin (Sprint 12: passo "APIs externas" removido) -

@router.post("/admin", response_model=UserOut)
def create_first_admin(payload: SetupAdminStep):
    _guard_open()
    db = SessionLocal()
    try:
        if _has_admin():
            raise HTTPException(status.HTTP_409_CONFLICT, "Ja existe um administrador")
        if db.query(User).filter((User.username == payload.username) | (User.email == payload.email)).first():
            raise HTTPException(status.HTTP_409_CONFLICT, "Username ou e-mail ja cadastrado")
        user = User(
            username=payload.username,
            email=payload.email,
            full_name=payload.full_name,
            hashed_password=hash_password(payload.password),
            role="admin",
            must_change_password=False,
        )
        db.add(user)
        db.flush()
        for a in ("view", "export", "schedule"):
            db.add(UserActionPermission(user_id=user.id, action=a))
        db.commit()
        db.refresh(user)
        audit.log(db, action="setup.admin_created",
                  detail=f"Admin '{payload.username}' criado pelo Wizard")
        return UserOut(
            id=user.id, username=user.username, email=user.email,
            full_name=user.full_name, role=user.role, is_active=user.is_active,
            must_change_password=user.must_change_password,
            last_login_at=user.last_login_at,
            allowed_tables=[], allowed_actions=["view", "export", "schedule"],
        )
    finally:
        db.close()


# ---- Finalizar ------------------------------------------------------------

@router.post("/finish")
def finish_setup():
    _guard_open()
    # Pre-requisitos minimos: DB + admin
    if not settings_store.get_setting("PROTHEUS_DB_URL"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Configure o banco antes de finalizar")
    if not _has_admin():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Crie o primeiro admin antes de finalizar")

    settings_store.set_setting("setup_complete", "true", scope="app",
                               description="Wizard finalizado")
    settings_store.set_setting("setup_completed_at", datetime.utcnow().isoformat(),
                               scope="app")
    db = SessionLocal()
    try:
        audit.log(db, action="setup.finished", detail="Wizard finalizado")
    finally:
        db.close()
    return {"detail": "Setup finalizado. Faca login para continuar."}


# Endpoint publico /api/branding/logo esta em backend/main.py.

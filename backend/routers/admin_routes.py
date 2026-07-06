"""Administracao em runtime — reload de config, restart controlado, health detail.

Endpoints (admin only):
- POST /api/admin/reload-config   — recarrega settings, recria engine Protheus,
                                    invalida cache, reinicia scheduler.
- POST /api/admin/restart         — `os._exit(3)` para forcar restart pelo
                                    supervisor (systemd `Restart=always` em LXC
                                    ou `scripts/supervisor.py` em dev).
- GET  /api/admin/health/detail   — status detalhado: Protheus, Redis, scheduler,
                                    pool de conexoes, contagem de jobs.

Reload e protegido por `threading.Lock` para evitar reentrada concorrente.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy import create_engine, text

from .. import audit, protheus_api, scheduler as scheduler_mod
from ..database import SessionLocal
from ..deps import get_client_ip, require_admin
from ..email_service import send_email_raw
from ..models import ActiveSession, Job, User
from ..schemas import (
    SetupBrandingStep, SetupDbStep, SetupSmtpStep,
)
from ..security import settings_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

_reload_lock = threading.Lock()

# Settings que NAO podem ser hot-reload (exigem restart real do processo).
RESTART_REQUIRED_KEYS = {"JWT_SECRET", "APP_PORT", "MASTER_KEY"}


@router.post("/reload-config")
def reload_config(request: Request, admin=Depends(require_admin)):
    """Aplica novas configuracoes sem derrubar o servidor.

    Operacoes:
    1. Invalida cache do `settings_store`.
    2. Reseta `EngineRegistry` (proximo SELECT reconstroi com pool atualizado).
    3. Reinicia o APScheduler (re-le timezone e cron expressions).
    4. Para credenciais SMTP, o `email_service` ja le do settings_store a cada chamada.

    Itens que exigem restart real entram em `requires_restart`. O frontend
    mostra um aviso pro admin chamar `POST /api/admin/restart`.
    """
    if not _reload_lock.acquire(blocking=False):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Outro reload ja esta em andamento — aguarde alguns segundos",
        )
    try:
        reloaded = []
        errors = []

        # 1) Cache de settings
        try:
            settings_store.invalidate_cache()
            reloaded.append("settings_cache")
        except Exception as exc:
            errors.append(f"settings_cache: {exc}")

        # 2) Engine Protheus
        try:
            protheus_api.engine_registry.reset()
            reloaded.append("protheus_engine")
        except Exception as exc:
            errors.append(f"protheus_engine: {exc}")

        # 3) Scheduler
        try:
            scheduler_mod.shutdown()
            time.sleep(0.2)
            scheduler_mod.start()
            reloaded.append("scheduler")
        except Exception as exc:
            errors.append(f"scheduler: {exc}")

        db = SessionLocal()
        try:
            audit.log(
                db, action="admin.reload_config", user=admin, ip=get_client_ip(request),
                detail=f"reloaded={','.join(reloaded)} errors={len(errors)}",
                success=(not errors),
            )
        finally:
            db.close()

        return {
            "reloaded": reloaded,
            "errors": errors,
            "requires_restart": sorted(RESTART_REQUIRED_KEYS),
            "timestamp": datetime.utcnow().isoformat(),
        }
    finally:
        _reload_lock.release()


@router.post("/restart")
def restart_process(request: Request, admin=Depends(require_admin)):
    """Reinicia o processo via os._exit(3).

    Mecanica:
    - Em LXC prod, systemd com `Restart=always` re-spawn o processo em ~1s.
    - Em dev, `scripts/supervisor.py` deve estar rodando o servico — ao detectar
      exit code 3, ele re-spawn o uvicorn.
    - Sessoes JWT existentes continuam validas apos o restart (chave nao mudou).

    A resposta volta ANTES do exit (usamos thread defer com 1s) para o cliente
    receber 200 OK.
    """
    db = SessionLocal()
    try:
        audit.log(db, action="admin.restart", user=admin, ip=get_client_ip(request),
                  detail="Restart solicitado via UI")
    finally:
        db.close()

    def _delayed_exit():
        time.sleep(1.0)
        logger.warning("Restart via admin endpoint — saindo com exit code 3")
        os._exit(3)

    threading.Thread(target=_delayed_exit, daemon=True).start()
    return {"detail": "Reiniciando em 1s — pagina sera recarregavel em ~5s"}


@router.get("/health/detail")
def health_detail(admin=Depends(require_admin)):
    """Health-check completo. Cada componente reportado isoladamente."""
    out = {"timestamp": datetime.utcnow().isoformat(), "components": {}}

    # 1) Protheus (SELECT 1)
    try:
        with protheus_api.engine_registry.get().connect() as conn:
            conn.execute(text("SELECT 1")).scalar()
        eng = protheus_api.engine_registry.get()
        pool = eng.pool
        out["components"]["protheus"] = {
            "ok": True,
            "pool_size": getattr(pool, "size", lambda: None)(),
            "checked_out": getattr(pool, "checkedout", lambda: None)(),
            "overflow": getattr(pool, "overflow", lambda: None)(),
        }
    except Exception as exc:
        out["components"]["protheus"] = {"ok": False, "error": str(exc)}

    # 2) Scheduler
    try:
        running = scheduler_mod.scheduler.running
        jobs = [
            {"id": j.id, "next": str(j.next_run_time)}
            for j in scheduler_mod.scheduler.get_jobs()
        ]
        out["components"]["scheduler"] = {"ok": running, "jobs": jobs}
    except Exception as exc:
        out["components"]["scheduler"] = {"ok": False, "error": str(exc)}

    # 3) Broker da fila (Redis OU SQLite no modo dev)
    try:
        from ..queue.celery_app import _broker_url, _mode
        if _broker_url.startswith("sqla+") or _broker_url.startswith("db+"):
            # Modo dev SQLite — broker e' arquivo local, sem dependencia externa
            out["components"]["broker"] = {
                "ok": True,
                "type": "sqlite",
                "mode": _mode,
                "url": _mask(_broker_url),
                "note": "Modo dev sem Redis. Producao LXC usa Redis nativo via apt.",
            }
        else:
            import redis
            r = redis.Redis.from_url(_broker_url, socket_connect_timeout=2)
            r.ping()
            out["components"]["broker"] = {
                "ok": True, "type": "redis", "mode": _mode, "url": _mask(_broker_url),
            }
    except Exception as exc:
        out["components"]["broker"] = {"ok": False, "type": "unknown", "error": str(exc)}

    # 4) Jobs
    try:
        db = SessionLocal()
        try:
            from sqlalchemy import func
            counts = dict(db.query(Job.status, func.count(Job.id)).group_by(Job.status).all())
        finally:
            db.close()
        out["components"]["jobs"] = {"ok": True, "by_status": counts}
    except Exception as exc:
        out["components"]["jobs"] = {"ok": False, "error": str(exc)}

    # 5) Setup
    out["components"]["setup"] = {
        "ok": True,
        "setup_complete": settings_store.setup_complete(),
    }

    out["ok"] = all(c.get("ok") for c in out["components"].values())
    return out


def _mask(url: str) -> str:
    """Mascara senha em URLs (so para nao vazar no health)."""
    import re
    return re.sub(r":[^:@/]+@", ":***@", url)


def _mask_url(url: str) -> str:
    """Sprint 8 Part 3 — para webhooks que tem o token na propria path:
    mantem `scheme://host/<inicio>...` e oculta o resto.
    Ex: https://hooks.slack.com/services/T01/B01/ABC123 ->
        https://hooks.slack.com/services/T01/B01/***
    """
    if not url:
        return ""
    try:
        # Mantem ate o 4o "/" do path; o resto vira "***"
        parts = url.split("/")
        if len(parts) > 5:
            return "/".join(parts[:5] + ["***"])
        return url
    except Exception:
        return "***"


# ============================================================
#  Editor de Configuracoes (post-Wizard) — admin
# ============================================================

BRANDING_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "branding"
BRANDING_DIR.mkdir(parents=True, exist_ok=True)


@router.get("/config")
def get_config(admin=Depends(require_admin)):
    """Snapshot mascarado das configuracoes atuais (sem revelar secrets)."""
    def _g(key, default=""):
        return settings_store.get_setting(key, default) or default
    db_url = _g("PROTHEUS_DB_URL")
    return {
        "operation": {
            # Lista canonica de filiais — alimenta o Builder em todas as paginas.
            "branches": _g("BRANCHES_LIST", "01,02,03,04,05,06,07,08"),
        },
        "branding": {
            "app_name":     _g("APP_NAME", "Protheus Reports"),
            "primary_color":_g("PRIMARY_COLOR", "#2E8B3D"),
            "logo_url":     "/api/branding/logo",
        },
        "db": {
            "url_masked":   _mask(db_url) if db_url else "",
            "pool_size":    int(_g("PROTHEUS_POOL_SIZE", 20)),
            "max_overflow": int(_g("PROTHEUS_MAX_OVERFLOW", 30)),
        },
        "smtp": {
            "host":     _g("SMTP_HOST"),
            "port":     int(_g("SMTP_PORT", 587)),
            "user":     _g("SMTP_USER"),
            "sender":   _g("SMTP_FROM"),
            "use_tls":  str(_g("SMTP_USE_TLS", "true")).lower() in ("1","true","yes"),
            # password NAO retornado
        },
        "fiscal": {
            # Sprint 12: motor interno (SDS/SDT vs SF1/SD1). Sem fonte externa.
            "notify_email":         _g("FISCAL_NOTIFY_EMAIL"),
            "auto_branches":        _g("FISCAL_AUTO_BRANCHES"),
            "tolerance_valor_rs":   _g("FISCAL_TOLERANCE_VALOR_RS", "0.05"),
            "tolerance_icms_rs":    _g("FISCAL_TOLERANCE_ICMS_RS", "0.02"),
            "tolerance_quant":      _g("FISCAL_TOLERANCE_QUANT", "0.01"),
            "validate_ncm":         str(_g("FISCAL_VALIDATE_NCM", "true")).lower() in ("1","true","yes"),
            # Webhook de alerta (URL truncada para nao vazar token)
            "webhook_configured":   bool(_g("FISCAL_WEBHOOK_URL")),
            "webhook_url_preview":  _mask_url(_g("FISCAL_WEBHOOK_URL")),
        },
    }


@router.post("/config/branding")
def post_branding(payload: SetupBrandingStep, request: Request, admin=Depends(require_admin)):
    settings_store.set_setting("APP_NAME", payload.app_name, scope="branding",
                               updated_by_id=admin.id)
    if payload.primary_color:
        settings_store.set_setting("PRIMARY_COLOR", payload.primary_color,
                                   scope="branding", updated_by_id=admin.id)
    db = SessionLocal()
    try:
        audit.log(db, action="admin.config.branding", user=admin, ip=get_client_ip(request),
                  detail=f"app_name={payload.app_name}")
    finally:
        db.close()
    return {"detail": "Branding atualizado"}


@router.post("/config/branding/logo")
async def post_branding_logo(logo: UploadFile = File(...), admin=Depends(require_admin)):
    if logo.content_type not in {"image/png", "image/jpeg", "image/svg+xml"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Use PNG/JPG/SVG")
    target = BRANDING_DIR / "logo.png"
    async with aiofiles.open(target, "wb") as f:
        while chunk := await logo.read(64 * 1024):
            await f.write(chunk)
    return {"detail": "Logo atualizado", "path": "/api/branding/logo"}


@router.post("/config/db")
def post_db(payload: SetupDbStep, request: Request, admin=Depends(require_admin)):
    settings_store.set_setting("PROTHEUS_DB_URL", payload.db_url,
                               is_secret=True, scope="db", updated_by_id=admin.id)
    settings_store.set_setting("PROTHEUS_POOL_SIZE", payload.pool_size, scope="db",
                               updated_by_id=admin.id)
    settings_store.set_setting("PROTHEUS_MAX_OVERFLOW", payload.max_overflow, scope="db",
                               updated_by_id=admin.id)
    protheus_api.engine_registry.reset()
    db = SessionLocal()
    try:
        audit.log(db, action="admin.config.db", user=admin, ip=get_client_ip(request))
    finally:
        db.close()
    return {"detail": "Banco atualizado e engine reiniciada"}


@router.post("/test/db")
def admin_test_db(payload: SetupDbStep, admin=Depends(require_admin)):
    try:
        engine = create_engine(payload.db_url, pool_pre_ping=True, future=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1")).scalar()
        engine.dispose()
        return {"ok": True, "detail": "Conexao bem-sucedida"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


@router.post("/config/smtp")
def post_smtp(payload: dict, request: Request, admin=Depends(require_admin)):
    """Atualiza SMTP. Aceita `password` vazio para manter a atual.

    Campos esperados (todos opcionais excetos host/port/user):
      host, port, user, password (opcional), sender, use_tls
    """
    if "host" in payload:
        settings_store.set_setting("SMTP_HOST", payload["host"], scope="smtp", updated_by_id=admin.id)
    if "port" in payload:
        settings_store.set_setting("SMTP_PORT", int(payload["port"]), scope="smtp", updated_by_id=admin.id)
    if "user" in payload:
        settings_store.set_setting("SMTP_USER", payload["user"], scope="smtp", updated_by_id=admin.id)
    # Password: so atualiza se vier nao-vazio
    pwd = payload.get("password")
    if pwd:
        settings_store.set_setting("SMTP_PASSWORD", pwd, is_secret=True,
                                   scope="smtp", updated_by_id=admin.id)
    if "sender" in payload:
        settings_store.set_setting("SMTP_FROM", str(payload["sender"]), scope="smtp",
                                   updated_by_id=admin.id)
    if "use_tls" in payload:
        settings_store.set_setting("SMTP_USE_TLS", str(payload["use_tls"]).lower(),
                                   scope="smtp", updated_by_id=admin.id)
    db = SessionLocal()
    try:
        audit.log(db, action="admin.config.smtp", user=admin, ip=get_client_ip(request),
                  detail="password_changed=" + ("yes" if pwd else "no"))
    finally:
        db.close()
    return {"detail": "SMTP atualizado", "password_changed": bool(pwd)}


@router.post("/test/smtp")
def admin_test_smtp(payload: SetupSmtpStep, admin=Depends(require_admin)):
    try:
        send_email_raw(
            host=payload.host, port=payload.port, user=payload.user,
            password=payload.password, use_tls=payload.use_tls,
            sender=str(payload.sender), to=[str(payload.sender)],
            subject="[Teste] Protheus Reports — SMTP",
            body="Teste de configuracao SMTP via Administracao.",
        )
        return {"ok": True, "detail": "E-mail enviado para o proprio remetente"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


@router.post("/config/operation")
def post_operation(payload: dict, request: Request, admin=Depends(require_admin)):
    """Edita parametros operacionais — atualmente a lista de filiais.

    Aceita `branches` como string CSV ("01,02,03") ou lista.
    """
    branches = payload.get("branches")
    if isinstance(branches, list):
        branches = ",".join(str(b).strip() for b in branches if str(b).strip())
    if not isinstance(branches, str) or not branches:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Informe ao menos uma filial (CSV ou lista)",
        )
    # Normaliza: tudo em uppercase, sem espacos, dedup preservando ordem
    seen: set[str] = set()
    norm: list[str] = []
    for b in branches.split(","):
        b = b.strip().upper()
        if b and b not in seen:
            seen.add(b); norm.append(b)
    if not norm:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Lista de filiais invalida")

    csv = ",".join(norm)
    settings_store.set_setting("BRANCHES_LIST", csv, scope="operation",
                               updated_by_id=admin.id,
                               description="Filiais da operacao (CSV)")
    db = SessionLocal()
    try:
        audit.log(db, action="admin.config.operation", user=admin,
                  ip=get_client_ip(request), detail=f"branches={csv}")
    finally:
        db.close()
    return {"detail": "Filiais atualizadas", "branches": norm}


# ============================================================
#  Sprint 11 — Auditor Fiscal autonomo (cron configuravel)
# ============================================================

@router.get("/config/fiscal-schedule")
def get_fiscal_schedule(admin=Depends(require_admin)):
    """Lista opcoes do cron + schedule atual + proximo fire-time."""
    from .. import scheduler as scheduler_mod
    options = [
        {"key": k, "label": v["label"]}
        for k, v in scheduler_mod.FISCAL_SCHEDULE_OPTIONS.items()
    ]
    current = scheduler_mod._current_fiscal_schedule_key()
    job = None
    try:
        job = scheduler_mod.scheduler.get_job("fiscal_tick")
    except Exception:
        pass
    next_run = job.next_run_time.isoformat() if job and job.next_run_time else None
    return {
        "options": options,
        "current": current,
        "label": scheduler_mod.FISCAL_SCHEDULE_OPTIONS.get(current, {}).get("label", current),
        "next_run_time": next_run,
        "enabled": next_run is not None,
    }


@router.post("/config/fiscal-schedule")
def post_fiscal_schedule(
    payload: dict, request: Request, admin=Depends(require_admin),
):
    """Atualiza o cron do Auditor Fiscal autonomo + recarrega o scheduler."""
    from .. import scheduler as scheduler_mod
    key = (payload.get("schedule") or "").strip()
    if key not in scheduler_mod.FISCAL_SCHEDULE_OPTIONS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"schedule invalida: '{key}'. Use: "
            f"{list(scheduler_mod.FISCAL_SCHEDULE_OPTIONS.keys())}",
        )
    settings_store.set_setting(
        "FISCAL_AUTO_SCHEDULE", key,
        scope="fiscal", updated_by_id=admin.id,
        description="Cron do Auditor Fiscal autonomo (Sprint 11)",
    )
    info = scheduler_mod.reload_fiscal_schedule()
    db = SessionLocal()
    try:
        audit.log(db, action="admin.config.fiscal_schedule", user=admin,
                  ip=get_client_ip(request), detail=f"schedule={key}")
    finally:
        db.close()
    return {"detail": "Cron do Auditor atualizado", **info}


@router.post("/config/fiscal")
def post_fiscal_options(payload: dict, request: Request, admin=Depends(require_admin)):
    """Opcoes do Auditor Fiscal — Sprint 12 (motor interno).

    Sem mais FISCAL_SOURCE (motor e' fixo: interno SDS/SDT vs SF1/SD1).
    """
    allowed = {
        "FISCAL_NOTIFY_EMAIL",
        "FISCAL_AUTO_BRANCHES",
        # Tolerancias (R$ e quantidade)
        "FISCAL_TOLERANCE_VALOR_RS",
        "FISCAL_TOLERANCE_ICMS_RS",
        "FISCAL_TOLERANCE_QUANT",
        "FISCAL_VALIDATE_NCM",
    }
    saved = []
    for k, v in payload.items():
        if k in allowed and v is not None:
            settings_store.set_setting(k, v, scope="fiscal", updated_by_id=admin.id)
            saved.append(k)
    db = SessionLocal()
    try:
        audit.log(db, action="admin.config.fiscal", user=admin, ip=get_client_ip(request),
                  detail=",".join(saved))
    finally:
        db.close()
    return {"detail": "Opcoes do Auditor Fiscal atualizadas", "saved": saved}


# ============================================================
#  Catalogo de Erros — admin
# ============================================================

@router.get("/email/preview/fiscal")
def preview_fiscal_email(admin=Depends(require_admin)):
    """Renderiza o template do e-mail de anomalias com dados MOCK.

    Util para o admin validar a aparencia (especialmente o bloco vermelho
    de NCM) ANTES de uma execucao real do Auditor.

    Retorna HTML puro — o frontend abre num iframe modal.
    """
    from datetime import datetime
    from ..fiscal.auditor import _render_email

    mock_anomalies = [
        {"doc_key": "35200312345678000199550010000000019000000019",
         "branch": "01", "field": "item_001_ncm", "severity": "critical",
         "protheus_value": "84733041", "xml_value": "84733042"},
        {"doc_key": "35200345678901000111550010000000023000000023",
         "branch": "01", "field": "item_002_ncm", "severity": "critical",
         "protheus_value": "73181500", "xml_value": "73181400"},
        {"doc_key": "35200398765432000122550010000000007000000007",
         "branch": "02", "field": "valor_total", "severity": "warn",
         "protheus_value": "1250.30", "xml_value": "1250.45"},
        {"doc_key": "35200345678901000111550010000000023000000023",
         "branch": "01", "field": "item_002_cfop", "severity": "critical",
         "protheus_value": "1102", "xml_value": "5102"},
        {"doc_key": "35200398765432000122550010000000007000000007",
         "branch": "02", "field": "cnpj_fornecedor", "severity": "critical",
         "protheus_value": "12345678000199", "xml_value": "98765432000122"},
        {"doc_key": "35200345678901000111550010000000023000000023",
         "branch": "01", "field": "se2_parcela_1_valor", "severity": "warn",
         "protheus_value": "500.00", "xml_value": "500.50"},
    ]
    stats = {
        "docs_audited": 42, "anomalies": len(mock_anomalies), "critical": 4,
        "ncm_divergences": 2,
        "period": "01/05/2026 a 14/05/2026 (PREVIEW)",
    }
    html = _render_email(mock_anomalies, stats)
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html, status_code=200)


# ============================================================
#  Sprint 8 Part 3 — Webhook de alertas fiscais (Teams/Slack/generico)
# ============================================================

@router.post("/config/webhook")
def post_webhook(payload: dict, request: Request, admin=Depends(require_admin)):
    """Salva FISCAL_WEBHOOK_URL. Campo unico — apaga setando string vazia.

    Aceita qualquer URL HTTPS (Slack: https://hooks.slack.com/...,
    Teams: https://outlook.office.com/webhook/..., generico: qualquer rota).
    """
    url = (payload.get("url") or "").strip()
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "URL deve comecar com http:// ou https://",
        )
    # Tratado como secret (a URL contem token de autenticacao em muitos casos)
    settings_store.set_setting(
        "FISCAL_WEBHOOK_URL", url, is_secret=True, scope="fiscal",
        updated_by_id=admin.id,
        description="URL do webhook (Slack/Teams/generico) p/ alertas criticos do auditor fiscal",
    )
    db = SessionLocal()
    try:
        audit.log(db, action="admin.config.webhook", user=admin, ip=get_client_ip(request),
                  detail=f"url_set={bool(url)}")
    finally:
        db.close()
    return {"detail": "Webhook atualizado", "configured": bool(url)}


@router.post("/test/webhook")
def test_webhook(payload: dict, admin=Depends(require_admin)):
    """Dispara uma mensagem de teste para a URL configurada.

    NAO requer rodar auditoria — util para validar a integracao logo
    apos colar a URL.
    """
    from ..fiscal.webhook import send_test_message, is_configured
    if not is_configured():
        return {"ok": False, "detail": "URL nao configurada — preencha o campo primeiro"}
    custom = (payload.get("text") or "").strip() or None
    return send_test_message(custom)


@router.get("/error-catalog")
def get_error_catalog(admin=Depends(require_admin)):
    """Serve o conteudo de docs/ERROR_CATALOG.md para o modal de Admin."""
    catalog_path = Path(__file__).resolve().parent.parent.parent / "docs" / "ERROR_CATALOG.md"
    if not catalog_path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Catalogo nao encontrado")
    return {
        "filename": "ERROR_CATALOG.md",
        "content": catalog_path.read_text(encoding="utf-8"),
    }


# ============================================================
#  Sprint 6 — Dicionario SX3 (humanizacao de headers)
# ============================================================

@router.get("/sx3/stats")
def sx3_stats(admin=Depends(require_admin)):
    """Estatisticas do cache SX3 (admin)."""
    from .. import dict_sx3
    return dict_sx3.stats()


@router.post("/sx3/reload")
def sx3_reload(request: Request, admin=Depends(require_admin)):
    """Recarrega o dicionario SX3 do Protheus.

    Util quando o cliente customiza X3_TITULO no Configurador e quer que os
    novos titulos apareçam nos Excels exportados sem precisar reiniciar.
    """
    from .. import dict_sx3
    result = dict_sx3.load_sx3(force=True)
    db = SessionLocal()
    try:
        audit.log(db, action="admin.sx3.reload", user=admin, ip=get_client_ip(request),
                  detail=f"loaded={result.get('count', 0)} source={result.get('source')}")
    finally:
        db.close()
    return result


# ============================================================
#  Sprint 6 — Gestao de Sessoes Ativas
# ============================================================

@router.get("/sessions")
def list_sessions(admin=Depends(require_admin), include_revoked: bool = False):
    """Lista sessoes JWT atualmente validas.

    Por padrao retorna apenas as ativas (nao-revogadas e nao-expiradas). Use
    `include_revoked=true` para historico.
    """
    db = SessionLocal()
    try:
        q = (
            db.query(ActiveSession, User)
            .join(User, User.id == ActiveSession.user_id)
        )
        if not include_revoked:
            q = q.filter(
                ActiveSession.revoked_at.is_(None),
                ActiveSession.expires_at > datetime.utcnow(),
            )
        q = q.order_by(ActiveSession.created_at.desc())
        out = []
        now = datetime.utcnow()
        for sess, user in q.all():
            out.append({
                "jti": sess.jti,
                "user_id": sess.user_id,
                "username": user.username,
                "email": user.email,
                "full_name": user.full_name,
                "role": user.role,
                "created_at": sess.created_at.isoformat() if sess.created_at else None,
                "expires_at": sess.expires_at.isoformat() if sess.expires_at else None,
                "revoked_at": sess.revoked_at.isoformat() if sess.revoked_at else None,
                "ip_address": sess.ip_address,
                "user_agent": sess.user_agent,
                "is_active": (sess.revoked_at is None and sess.expires_at > now),
                "is_self": (sess.user_id == admin.id),
            })
        return {"total": len(out), "sessions": out}
    finally:
        db.close()


@router.delete("/sessions/{jti}")
def revoke_session(jti: str, request: Request, admin=Depends(require_admin)):
    """Derruba uma sessao especifica (revoga o jti).

    O proximo request que esse token tentar fazer sera bloqueado em
    `deps.get_current_user` (ja confere ActiveSession.revoked_at). Util quando
    um usuario esqueceu logout em outra maquina e a conta ficou no limite
    de 3 sessoes simultaneas.
    """
    db = SessionLocal()
    try:
        sess = db.query(ActiveSession).filter(ActiveSession.jti == jti).first()
        if not sess:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Sessao nao encontrada")
        if sess.revoked_at:
            return {"detail": "Sessao ja estava revogada", "jti": jti}

        sess.revoked_at = datetime.utcnow()
        db.commit()

        target_user = db.query(User).filter(User.id == sess.user_id).first()
        target_name = target_user.username if target_user else f"id={sess.user_id}"
        audit.log(db, action="admin.session.revoke", user=admin,
                  ip=get_client_ip(request),
                  detail=f"target={target_name} jti={jti[:8]}...")
        return {"detail": f"Sessao de {target_name} revogada", "jti": jti}
    finally:
        db.close()


@router.delete("/sessions/user/{user_id}")
def revoke_all_user_sessions(user_id: int, request: Request, admin=Depends(require_admin)):
    """Revoga TODAS as sessoes ativas de um usuario — uso emergencial.

    Cenarios: conta comprometida, demissao, troca obrigatoria de senha. O
    proprio admin nao pode usar esta rota contra si mesmo (use logout normal).
    """
    if user_id == admin.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Voce nao pode revogar suas proprias sessoes por aqui — use logout",
        )
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario nao encontrado")
        now = datetime.utcnow()
        affected = (
            db.query(ActiveSession)
            .filter(
                ActiveSession.user_id == user_id,
                ActiveSession.revoked_at.is_(None),
                ActiveSession.expires_at > now,
            )
            .update({"revoked_at": now}, synchronize_session=False)
        )
        db.commit()
        audit.log(db, action="admin.session.revoke_all", user=admin,
                  ip=get_client_ip(request),
                  detail=f"target={user.username} revoked={affected}")
        return {"detail": f"{affected} sessoes revogadas para {user.username}",
                "revoked_count": affected}
    finally:
        db.close()

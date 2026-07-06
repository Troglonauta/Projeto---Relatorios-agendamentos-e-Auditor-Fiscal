"""Envio de e-mails via SMTP — templates HTML responsivos (Sprint 5).

Le credenciais via `settings_store` (SQLite criptografado, Fase 3).
Fallback para variaveis de ambiente quando o setting ainda nao foi gravado.

Templates ficam em `backend/email_templates/` — sao HTML puro com placeholders
`{{ VAR }}`. Engine de substituicao simples (sem Jinja para nao adicionar
dependencia) — suficiente para 3 templates transacionais.

Templates disponiveis:
- `password_reset.html` — recuperacao de senha (usado por send_temp_password)
- `welcome.html`        — boas-vindas a novo usuario (admin cria)
- `report.html`         — entrega de relatorio agendado por e-mail
"""
from __future__ import annotations

import logging
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from .config import get_settings
from .security import settings_store

logger = logging.getLogger(__name__)
settings = get_settings()
TZ_BR = ZoneInfo("America/Sao_Paulo")
TEMPLATES_DIR = Path(__file__).parent / "email_templates"

MIME_MAP = {
    "xlsx": ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "csv":  ("text", "csv"),
    "pdf":  ("application", "pdf"),
    "ods":  ("application", "vnd.oasis.opendocument.spreadsheet"),
}


# ============================================================
#  Template engine — substituicao simples {{VAR}} sem Jinja
# ============================================================

def _render_template(name: str, ctx: dict) -> str:
    """Carrega template HTML e substitui placeholders {{KEY}} por valores.

    Se a key nao for fornecida, mantem o placeholder (facilita debug).
    Engine deliberadamente simples — clientes de e-mail nao suportam logica
    complexa de qualquer forma.
    """
    path = TEMPLATES_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Template '{name}' nao encontrado em {TEMPLATES_DIR}")
    text = path.read_text(encoding="utf-8")
    for key, value in ctx.items():
        text = text.replace("{{" + key + "}}", str(value) if value is not None else "")
        text = text.replace("{{ " + key + " }}", str(value) if value is not None else "")
    return text


def _render_full(template_name: str, **ctx) -> str:
    """Renderiza um template transacional dentro do `_base.html`.

    `ctx` deve trazer pelo menos: SUBJECT, PREHEADER, HEADER_TITLE, HEADER_SUBTITLE.
    O template especifico vira no placeholder `BODY` do base.
    """
    app_name = settings_store.get_setting("APP_NAME") or settings.APP_NAME
    # Logo via endpoint publico — qualquer cliente de e-mail pode buscar.
    # Em ambientes sem DNS publico, configure `APP_PUBLIC_URL` para usar caminho absoluto.
    public_url = settings_store.get_setting("APP_PUBLIC_URL") or ""
    logo_url = f"{public_url.rstrip('/')}/api/branding/logo" if public_url else "/api/branding/logo"
    login_url = f"{public_url.rstrip('/')}/static/pages/login.html" if public_url else "/static/pages/login.html"

    base_ctx = {
        "APP_NAME":         app_name,
        "LOGO_URL":         logo_url,
        "LOGIN_URL":        login_url,
        "GENERATED_AT":     datetime.now(TZ_BR).strftime("%d/%m/%Y %H:%M"),
        "FOOTER_LINE":      "extracao e auditoria do Protheus",
        "CTA_BLOCK":        "",   # default: sem CTA dedicado (alguns templates preenchem)
        "PREHEADER":        ctx.get("PREHEADER") or ctx.get("SUBJECT", ""),
        "HEADER_SUBTITLE":  "",
    }
    base_ctx.update(ctx)

    # 1) Renderiza o template transacional (corpo)
    body = _render_template(template_name, base_ctx)
    base_ctx["BODY"] = body

    # 2) Embrulha no _base.html
    return _render_template("_base.html", base_ctx)


# ============================================================
#  SMTP — config + envio
# ============================================================

def _smtp_config() -> dict:
    def _get(key, default):
        v = settings_store.get_setting(key, default)
        return v if v not in (None, "") else default
    use_tls = str(_get("SMTP_USE_TLS", settings.SMTP_USE_TLS)).lower() in ("1", "true", "yes")
    return {
        "host":     _get("SMTP_HOST",     settings.SMTP_HOST),
        "port":     int(_get("SMTP_PORT", settings.SMTP_PORT)),
        "user":     _get("SMTP_USER",     settings.SMTP_USER),
        "password": _get("SMTP_PASSWORD", settings.SMTP_PASSWORD),
        "sender":   _get("SMTP_FROM",     settings.SMTP_FROM) or _get("SMTP_USER", settings.SMTP_USER),
        "use_tls":  use_tls,
    }


def _normalize_attachment(att) -> Optional[tuple[str, bytes, str, str]]:
    """Sprint 15 — normaliza um anexo para `(filename, content_bytes, maintype, subtype)`.

    Aceita:
      - `Path` ou `str` apontando para arquivo no disco
      - `dict(filename, content, mimetype)` para anexos in-memory
        (ex: XLSX gerado em `io.BytesIO`)

    Retorna `None` se o anexo for invalido (arquivo inexistente / dict mal-formado);
    o caller decide se ignora ou levanta.
    """
    if att is None:
        return None
    # Caminho no disco
    if isinstance(att, (str, Path)):
        path = Path(att)
        if not path.exists():
            logger.warning("Anexo nao encontrado: %s", path)
            return None
        ext = path.suffix.lower().lstrip(".")
        maintype, subtype = MIME_MAP.get(ext, ("application", "octet-stream"))
        return (path.name, path.read_bytes(), maintype, subtype)
    # In-memory dict
    if isinstance(att, dict):
        filename = att.get("filename") or "anexo.bin"
        content = att.get("content")
        if content is None:
            logger.warning("Anexo in-memory sem 'content': %s", filename)
            return None
        if not isinstance(content, (bytes, bytearray)):
            logger.warning("Anexo '%s' precisa ser bytes (recebeu %s)",
                           filename, type(content).__name__)
            return None
        mimetype = att.get("mimetype") or ""
        if "/" in mimetype:
            maintype, subtype = mimetype.split("/", 1)
        else:
            ext = Path(filename).suffix.lower().lstrip(".")
            maintype, subtype = MIME_MAP.get(ext, ("application", "octet-stream"))
        return (filename, bytes(content), maintype, subtype)
    logger.warning("Tipo de anexo nao suportado: %s", type(att).__name__)
    return None


def send_email_raw(
    *,
    host: str, port: int, user: str, password: str, use_tls: bool,
    sender: str, to: Iterable[str], subject: str, body: str,
    html: Optional[str] = None,
    attachments: Optional[list] = None,
) -> None:
    """Envio low-level — recebe credenciais explicitas.

    `attachments` aceita lista mista de:
      - `Path`/`str` (arquivo no disco)
      - `dict(filename, content: bytes, mimetype)` para anexos in-memory
    """
    if not host:
        raise RuntimeError("SMTP nao configurado — preencha SMTP_* no Wizard")

    msg = EmailMessage()
    msg["From"] = sender or user
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")

    for att in attachments or []:
        normalized = _normalize_attachment(att)
        if not normalized:
            continue
        filename, content_bytes, maintype, subtype = normalized
        msg.add_attachment(
            content_bytes, maintype=maintype, subtype=subtype, filename=filename,
        )

    if use_tls:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls()
            if user:
                s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP_SSL(host, port, timeout=30) as s:
            if user:
                s.login(user, password)
            s.send_message(msg)


def send_email(
    *, to: Iterable[str], subject: str, body: str,
    html: Optional[str] = None, attachments: Optional[list] = None,
) -> None:
    """Envia e-mail usando credenciais salvas no settings_store.

    `attachments` aceita lista mista de `Path`/`str` e `dict(filename, content, mimetype)`.
    """
    cfg = _smtp_config()
    send_email_raw(
        host=cfg["host"], port=cfg["port"], user=cfg["user"], password=cfg["password"],
        use_tls=cfg["use_tls"], sender=cfg["sender"],
        to=to, subject=subject, body=body, html=html, attachments=attachments,
    )


# ============================================================
#  Templates transacionais — Sprint 5
# ============================================================

def send_temp_password(*, to: str, username: str, temp_password: str) -> None:
    """Recuperacao de senha — usa template HTML responsivo."""
    subject = "Recuperação de senha — Protheus Reports"
    body_txt = (
        f"Ola {username},\n\n"
        f"Sua senha temporaria: {temp_password}\n\n"
        f"Use no proximo login (valida por 2h). O sistema vai pedir a troca imediata.\n"
    )
    html = _render_full(
        "password_reset.html",
        SUBJECT=subject,
        PREHEADER=f"Senha temporaria valida por 2h para {username}",
        HEADER_TITLE="Recuperação de senha",
        HEADER_SUBTITLE="Sua senha temporária expirará em 2 horas",
        USERNAME=username,
        TEMP_PASSWORD=temp_password,
    )
    send_email(to=[to], subject=subject, body=body_txt, html=html)


def send_welcome(*, to: str, username: str, email: str, full_name: Optional[str],
                 role: str, temp_password: str) -> None:
    """Boas-vindas para novo usuario criado pelo admin."""
    app_name = settings_store.get_setting("APP_NAME") or settings.APP_NAME
    subject = f"Bem-vindo ao {app_name}"
    body_txt = (
        f"Ola {full_name or username},\n\n"
        f"Seu acesso ao {app_name} foi criado.\n"
        f"Usuario: {username}\n"
        f"Senha inicial: {temp_password}\n"
        f"Faca login e altere a senha no primeiro acesso.\n"
    )
    html = _render_full(
        "welcome.html",
        SUBJECT=subject,
        PREHEADER=f"Acesso criado para {username}",
        HEADER_TITLE=f"Bem-vindo ao {app_name}!",
        HEADER_SUBTITLE="Seu acesso foi criado pelo administrador",
        FULL_NAME=full_name or username,
        USERNAME=username,
        EMAIL=email,
        ROLE=role.upper(),
        TEMP_PASSWORD=temp_password,
    )
    send_email(to=[to], subject=subject, body=body_txt, html=html)


def send_report(*, to: list[str], report_name: str, table: str,
                row_count: int, period: str, file_format: str,
                attachment_name: str, attachments: list[Path],
                join_info: str = "—") -> None:
    """Entrega de relatorio agendado por e-mail."""
    subject = f"[Relatório] {report_name}"
    body_txt = (
        f"Relatorio '{report_name}' gerado.\n"
        f"Tabela base: {table}\n"
        f"Linhas: {row_count}\n"
        f"Periodo: {period}\n"
        f"Anexo: {attachment_name}\n"
    )
    html = _render_full(
        "report.html",
        SUBJECT=subject,
        PREHEADER=f"{row_count} linhas em {table}",
        HEADER_TITLE=f"📊 {report_name}",
        HEADER_SUBTITLE=f"Gerado em {datetime.now(TZ_BR).strftime('%d/%m/%Y %H:%M')}",
        REPORT_NAME=report_name,
        TABLE=table,
        ROW_COUNT=f"{row_count:,}".replace(",", "."),
        PERIOD=period,
        FORMAT=file_format.upper(),
        ATTACHMENT_NAME=attachment_name,
        JOIN_INFO=join_info,
    )
    send_email(to=to, subject=subject, body=body_txt, html=html, attachments=attachments)

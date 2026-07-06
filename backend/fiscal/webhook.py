"""Sprint 8 Part 3 — Webhook de alertas fiscais (Teams/Slack/genérico).

Quando o Auditor termina e detecta divergencias CRITICAS, dispara um POST
para a URL configurada em `AppSetting('FISCAL_WEBHOOK_URL')`.

Compatibilidade do payload:
- **Slack incoming webhook**: aceita `{"text": "..."}`
- **Microsoft Teams** (incoming webhook do canal): aceita `{"text": "..."}`
  ou MessageCard. Usamos `{"text": "..."}` por ser o denominador comum.
- **Webhooks genericos**: recebem o mesmo payload + `severity`/`stats` para
  consumir o que quiserem.

Best-effort: falhas de rede / 4xx / 5xx sao APENAS logadas — nunca abortam
a task Celery (auditoria ja terminou com sucesso, o webhook e' secundario).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import requests

from ..security import settings_store

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT_SECONDS = 8


def is_configured() -> bool:
    url = settings_store.get_setting("FISCAL_WEBHOOK_URL") or ""
    return bool(url.strip().startswith(("http://", "https://")))


def _build_message(stats: dict) -> dict[str, Any]:
    """Monta o payload {text, severity, stats} a partir das stats do auditor."""
    critical = int(stats.get("critical", 0) or 0)
    total    = int(stats.get("anomalies", 0) or 0)
    ncm      = int(stats.get("ncm_divergences", 0) or 0)
    pending  = int(stats.get("docs_pending", 0) or 0)
    period   = stats.get("period", "")

    # Texto principal (suporta Slack/Teams). Markdown leve, ASCII safe.
    if critical > 0:
        text = (
            f"🚨 *Alerta Fiscal — {critical} divergencia(s) CRITICA(s)* "
            f"encontrada(s) na auditoria de *{period}*."
        )
        if ncm > 0:
            text += f"\n• {ncm} divergencia(s) de NCM (risco SPED)."
        text += f"\n• Total de anomalias: {total}"
        if pending > 0:
            text += f" · {pending} XML(s) pendente(s) na fonte"
        text += "\n📊 Confira o painel: Auditor Fiscal > Anomalias"
    else:
        # Sem criticas — nao deveria entrar aqui (caller filtra), mas defensivo.
        text = f"✓ Auditoria de {period}: {total} anomalia(s), nenhuma critica."

    return {
        "text": text,
        # Campos auxiliares para webhooks genericos / Discord / etc.
        "severity": "critical" if critical > 0 else "info",
        "stats": {
            "period":  period,
            "total":   total,
            "critical": critical,
            "ncm":     ncm,
            "pending": pending,
            "branches": stats.get("branches", []),
        },
    }


def send_critical_alert(stats: dict) -> Optional[dict]:
    """Envia o alerta SE houver criticas E a URL estiver configurada.

    Retorna `{ok, status_code, message}` no log. Nao lanca — caller (worker)
    nao deve quebrar por causa de webhook.
    """
    critical = int(stats.get("critical", 0) or 0)
    if critical <= 0:
        logger.debug("Webhook: sem criticas, skip")
        return None
    if not is_configured():
        logger.info("Webhook: URL nao configurada — skip alerta de %d criticas", critical)
        return None

    url = settings_store.get_setting("FISCAL_WEBHOOK_URL").strip()
    payload = _build_message(stats)
    return _post(url, payload, context="critical_alert")


def send_test_message(custom_text: Optional[str] = None) -> dict:
    """Endpoint de teste — POST simples para validar a URL configurada."""
    if not is_configured():
        return {"ok": False, "detail": "URL nao configurada"}
    url = settings_store.get_setting("FISCAL_WEBHOOK_URL").strip()
    payload = {
        "text": custom_text or (
            "🧪 Teste de webhook do Protheus Reports — se voce esta vendo "
            "esta mensagem, a integracao esta funcionando!"
        ),
        "severity": "info",
        "test": True,
    }
    return _post(url, payload, context="test") or {"ok": False, "detail": "Sem resposta"}


def _post(url: str, payload: dict, context: str) -> dict:
    """Faz POST + JSON. Captura tudo, devolve dict serializavel para o caller."""
    try:
        r = requests.post(
            url, json=payload,
            headers={"Content-Type": "application/json"},
            timeout=WEBHOOK_TIMEOUT_SECONDS, verify=True,
        )
    except requests.RequestException as exc:
        logger.warning("Webhook (%s) falhou de rede: %s", context, exc)
        return {"ok": False, "detail": f"Falha de rede: {exc}"}

    # Slack/Teams costumam retornar 200 com body "ok" (Slack) ou vazio (Teams).
    if 200 <= r.status_code < 300:
        logger.info("Webhook (%s) OK: status=%s", context, r.status_code)
        return {"ok": True, "status_code": r.status_code, "detail": "Enviado"}

    logger.warning(
        "Webhook (%s) HTTP %s: %s", context, r.status_code, r.text[:200],
    )
    return {
        "ok": False, "status_code": r.status_code,
        "detail": f"HTTP {r.status_code}: {r.text[:200]}",
    }

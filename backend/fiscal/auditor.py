"""Orquestrador do Auditor Fiscal — Sprint 12 (Auditoria Interna).

Roda uma auditoria 100% interna ao Protheus:

  SDS (XML internalizado pelo ERP)  ⚔  SF1 (Nota classificada)
  SDT (Itens do XML)                ⚔  SD1 (Itens da nota)

Sem rede, sem parsing lxml, sem dependencia de Alterdata/SEFAZ/A1/TSS.

Fluxo:
1. Para cada filial, chama `_load_audit_period_internal` (4 queries SQL).
2. Para cada documento (`{sds, sf1, sdt_items, sd1_items}`), aplica
   `FiscalRuleEngine`. Anomalia "Nota Ausente" emerge naturalmente da
   ausencia de SF1 (LEFT JOIN sem match).
3. Persiste anomalias em `fiscal_anomalies` e dispara e-mail final.

NAO depende do Celery — pode ser chamado direto (sincrono curto) ou via
`fiscal_task.py` (assincrono longo).
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from .. import protheus_api
from ..database import SessionLocal
from ..email_service import send_email
from ..models import FiscalAnomaly
from ..security import settings_store
from ..timeutils import now_brt
from .internal_audit import _load_audit_period_internal
from .rule_engine import FiscalRuleEngine

logger = logging.getLogger(__name__)

TEMPLATE_PATH = Path(__file__).parent / "templates" / "anomaly_report.html"


# ---- Persistencia ---------------------------------------------------------

def _save_anomalies(
    db: Session, doc_key: str, branch: str, supplier_cnpj: Optional[str],
    divergences: list[dict], job_id: Optional[str], commit: bool = True,
) -> int:
    """Grava cada divergencia como linha em fiscal_anomalies. Retorna a contagem.

    `commit=False` acumula na sessao para o chamador commitar em LOTE (reduz a
    contencao do lock do SQLite web x worker — ver _run_internal_audit)."""
    n = 0
    for d in divergences:
        anomaly = FiscalAnomaly(
            doc_key=doc_key,
            branch=branch,
            supplier_cnpj=supplier_cnpj,
            field_compared=d["field"],
            protheus_value=str(d["protheus_value"]),
            xml_value=str(d["xml_value"]),
            severity=d["severity"],
            job_id=job_id,
        )
        db.add(anomaly)
        n += 1
    if n and commit:
        db.commit()
    return n


def _purge_prior_anomalies(
    db: Session, branches: list[str], engine_kind: str,
    chave_filter: Optional[str] = None,
) -> int:
    """Ponto 3 (2026) — "substituir ao rodar".

    Remove as anomalias ANTERIORES do mesmo escopo antes de gravar as novas,
    para a tela do Auditor nao acumular documentos de execucoes passadas.

    Escopo:
      - chave_filter setado -> apaga so a chave reauditada (desse motor).
      - senao               -> apaga as filiais auditadas (desse motor).
    O motor e' identificado pelo prefixo de `field_compared`:
      internal = sem prefixo ; financeiro = "fin_" ; comercial = "com_".
    """
    q = db.query(FiscalAnomaly)
    if chave_filter:
        q = q.filter(FiscalAnomaly.doc_key == chave_filter)
    elif branches:
        q = q.filter(FiscalAnomaly.branch.in_(list(branches)))
    else:
        return 0

    if engine_kind == "financeiro_se2":
        q = q.filter(FiscalAnomaly.field_compared.like("fin_%"))
    elif engine_kind == "comercial_sc5_se1":
        q = q.filter(FiscalAnomaly.field_compared.like("com_%"))
    else:  # internal — preserva o que pertence aos outros motores
        q = q.filter(~FiscalAnomaly.field_compared.like("fin_%"))
        q = q.filter(~FiscalAnomaly.field_compared.like("com_%"))

    n = q.delete(synchronize_session=False)
    if n:
        db.commit()
        logger.info(
            "Auditor: %d anomalia(s) anterior(es) substituida(s) (motor=%s)",
            n, engine_kind,
        )
    return n


def _doc_key(doc: dict) -> str:
    """Fix #4 — identificador estavel do documento para agrupamento.

    NFe/CTe com chave de 44 digitos -> usa a chave. Documentos SEM chave
    (NF avulsa, manuais) -> sintetiza `doc/serie/fornec/loja` (a UI e o
    endpoint /document-audit sabem reabrir por essas partes).
    """
    chave = (doc.get("chave") or "").strip()
    if len(chave) == 44 and chave.isdigit():
        return chave
    sds = doc.get("sds") or {}
    g = lambda k: str(sds.get(k) or "").strip()  # noqa: E731
    parts = [g("DS_DOC"), g("DS_SERIE"), g("DS_FORNEC"), g("DS_LOJA")]
    return "/".join(parts) if any(parts) else (chave or "(sem identificacao)")


def _save_doc_ok(
    db: Session, doc_key: str, branch: str, supplier_cnpj: Optional[str],
    job_id: Optional[str], commit: bool = True,
) -> None:
    """Fix #4 — marca um documento auditado SEM divergencia (severity='ok'),
    para o analista poder revisar TODOS os documentos. O toggle "apenas
    divergencias" filtra essas linhas; a contagem de divergencias as ignora.

    `commit=False` acumula para commit em LOTE (ver _run_internal_audit)."""
    db.add(FiscalAnomaly(
        doc_key=doc_key, branch=branch, supplier_cnpj=supplier_cnpj,
        field_compared="__documento__",
        protheus_value="documento auditado",
        xml_value="sem divergencia",
        severity="ok", job_id=job_id,
    ))
    if commit:
        db.commit()


# ---- E-mail ---------------------------------------------------------------

def _build_ncm_section(anomalies_summary: list[dict]) -> str:
    """Bloco vermelho destacado com NCMs divergentes.

    NCM e' o que mais gera multa em fiscalizacao — bloco aparece no topo do
    e-mail sempre que ha qualquer divergencia de NCM (Sprint 12: agora vem
    do par DT_NCM x D1_BM).
    """
    ncm = [a for a in anomalies_summary if "ncm" in a["field"].lower()]
    if not ncm:
        return ""
    rows = "".join(
        f"<tr><td><code>...{a['doc_key'][-8:]}</code></td>"
        f"<td>{a['branch']}</td>"
        f"<td><code>{a['protheus_value']}</code></td>"
        f"<td><code>{a['xml_value']}</code></td></tr>"
        for a in ncm[:50]
    )
    return f"""
    <tr>
      <td style="background:#C0392B;color:#fff;padding:18px 28px">
        <div style="font-size:11px;text-transform:uppercase;letter-spacing:1px;opacity:.9">COMPLIANCE FISCAL — NCM</div>
        <h2 style="margin:6px 0 12px;font-size:18px;font-weight:700">
          {len(ncm)} divergencia(s) de NCM detectada(s)
        </h2>
        <div style="font-size:13px;opacity:.95;margin-bottom:12px">
          NCM no item SD1.D1_BM nao bate com o XML internalizado SDT.DT_NCM.
          Risco direto de autuacao — resolva antes do fechamento fiscal.
        </div>
        <table cellpadding="6" cellspacing="0" style="width:100%;border-collapse:collapse;background:#fff;color:#1f2d3d;font-size:12px">
          <thead style="background:#f8d7da;color:#842029">
            <tr><th align="left">NFe (final)</th><th align="left">Filial</th>
                <th align="left">NCM Protheus (SD1)</th><th align="left">NCM XML (SDT)</th></tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
        {f'<div style="opacity:.85;font-size:11px;margin-top:8px">+{len(ncm)-50} divergencias adicionais — veja no painel.</div>' if len(ncm) > 50 else ''}
      </td>
    </tr>
    """


def _render_email(anomalies_summary: list[dict], stats: dict) -> str:
    """Carrega o template DETALHADO (lista de anomalias inline).

    Sprint 15: este caminho continua disponivel para o endpoint
    `/api/admin/email/preview/fiscal` (admin valida visual do template) mas
    NAO e' mais o usado pelo envio autonomo — o `_render_email_executive`
    substitui no fluxo de producao para deixar o corpo curto e gerencial.
    """
    if not TEMPLATE_PATH.exists():
        return f"<pre>{json.dumps(stats, indent=2, default=str)}\n\n{anomalies_summary}</pre>"
    html = TEMPLATE_PATH.read_text(encoding="utf-8")
    rows = "".join(
        f"<tr><td>{a['doc_key'][-6:]}</td><td>{a['branch']}</td>"
        f"<td>{a['field']}</td><td>{a['protheus_value']}</td>"
        f"<td>{a['xml_value']}</td><td>{a['severity']}</td></tr>"
        for a in anomalies_summary[:200]
    )
    ncm_section = _build_ncm_section(anomalies_summary)
    return (html
        .replace("{{NCM_SECTION}}", ncm_section)
        .replace("{{TOTAL_DOCS}}", str(stats.get("docs_audited", 0)))
        .replace("{{TOTAL_ANOMALIES}}", str(stats.get("anomalies", 0)))
        .replace("{{CRITICAL}}", str(stats.get("critical", 0)))
        .replace("{{PERIOD}}", stats.get("period", ""))
        .replace("{{ROWS}}", rows)
        .replace("{{GENERATED_AT}}", now_brt().strftime("%d/%m/%Y %H:%M"))
    )


def _render_email_executive(stats: dict) -> str:
    """Sprint 15 — corpo HTML CURTO e gerencial. Os detalhes vao em anexo.

    Quando ha NCM divergente, mantemos o bloco vermelho de alerta no topo
    (compliance critical) mas SEM listar cada caso — apenas o contador.
    """
    docs = stats.get("docs_audited", 0)
    anomalies = stats.get("anomalies", 0)
    critical = stats.get("critical", 0)
    nota_ausente = stats.get("nota_ausente", 0)
    ncm_div = stats.get("ncm_divergences", 0)
    period = stats.get("period", "")

    ncm_alert = ""
    if ncm_div:
        ncm_alert = (
            '<div style="background:#C0392B;color:#fff;padding:12px 16px;'
            'border-radius:6px;margin-bottom:14px;font-size:13px">'
            '<strong>COMPLIANCE FISCAL — NCM:</strong> '
            f'{ncm_div} divergencia(s) detectada(s). '
            'Veja o detalhamento na planilha em anexo (filtro Campo contendo "ncm").'
            '</div>'
        )

    ausente_alert = ""
    if nota_ausente:
        ausente_alert = (
            '<div style="background:#fff3cd;color:#664d03;padding:10px 14px;'
            'border-radius:6px;margin-bottom:14px;font-size:13px;'
            'border:1px solid #ffe69c">'
            f'<strong>Notas Ausentes:</strong> {nota_ausente} XML(s) importado(s) no ERP '
            'sem nota fiscal classificada (falta SF1).'
            '</div>'
        )

    return f"""<!doctype html>
<html><body style="font-family:Arial,sans-serif;color:#1f2d3d;max-width:640px;margin:0 auto;padding:20px">
  <div style="background:#2E8B3D;color:#fff;padding:18px 22px;border-radius:8px 8px 0 0">
    <div style="font-size:11px;text-transform:uppercase;letter-spacing:1px;opacity:.85">Auditor Fiscal — Resumo Executivo</div>
    <h1 style="margin:6px 0 0;font-size:20px;font-weight:700">Auditoria concluida</h1>
    <div style="font-size:13px;opacity:.9;margin-top:4px">Periodo: <strong>{period}</strong></div>
  </div>
  <div style="background:#fafbfc;border:1px solid #e6e9ee;border-top:0;border-radius:0 0 8px 8px;padding:20px 22px;font-size:14px;line-height:1.55">
    {ncm_alert}{ausente_alert}
    <p>A auditoria fiscal automatica foi concluida com os seguintes numeros:</p>
    <table cellpadding="8" cellspacing="0" style="border-collapse:collapse;margin:14px 0;font-size:13px">
      <tr>
        <td style="border:1px solid #e6e9ee;background:#fff"><strong>Documentos auditados</strong></td>
        <td style="border:1px solid #e6e9ee;background:#fff;text-align:right;font-variant-numeric:tabular-nums">{docs}</td>
      </tr>
      <tr>
        <td style="border:1px solid #e6e9ee;background:#fff"><strong>Divergencias encontradas</strong></td>
        <td style="border:1px solid #e6e9ee;background:#fff;text-align:right;font-variant-numeric:tabular-nums;color:{'#C0392B' if anomalies else '#1f2d3d'}">{anomalies}</td>
      </tr>
      <tr>
        <td style="border:1px solid #e6e9ee;background:#fff">Criticas</td>
        <td style="border:1px solid #e6e9ee;background:#fff;text-align:right;font-variant-numeric:tabular-nums;color:{'#C0392B' if critical else '#1f2d3d'}">{critical}</td>
      </tr>
      <tr>
        <td style="border:1px solid #e6e9ee;background:#fff">NCM divergentes</td>
        <td style="border:1px solid #e6e9ee;background:#fff;text-align:right;font-variant-numeric:tabular-nums">{ncm_div}</td>
      </tr>
      <tr>
        <td style="border:1px solid #e6e9ee;background:#fff">Notas Ausentes (XML sem SF1)</td>
        <td style="border:1px solid #e6e9ee;background:#fff;text-align:right;font-variant-numeric:tabular-nums">{nota_ausente}</td>
      </tr>
    </table>
    <p style="margin:18px 0 6px"><strong>Baixe a planilha em anexo</strong> para visualizar o detalhamento completo de cada divergencia, com todos os campos auditados (Match/Divergente/Sem dado).</p>
    <p style="color:#6b7480;font-size:12px;margin-top:18px;border-top:1px solid #e6e9ee;padding-top:10px">
      Relatorio gerado em {now_brt().strftime("%d/%m/%Y %H:%M")} (horario de Brasilia).<br>
      Mensagem automatica — Auditor Fiscal Interno (SDS/SDT vs SF1/SD1).
    </p>
  </div>
</body></html>"""


def _build_anomalies_xlsx_bytes(anomalies: list[dict], stats: dict) -> bytes:
    """Sprint 15 — gera o XLSX de detalhamento para anexar ao e-mail.

    Header verde Fertimaxi + AutoFit + freeze panes via helper compartilhado.
    Colunas fixas (mesmo schema do export pela UI).
    """
    from ..xlsx_utils import build_formatted_xlsx_bytes

    cols = [
        "Chave NFe", "Filial", "Campo Auditado",
        "Valor Protheus (SF1/SD1)", "Valor XML (SDS/SDT)", "Severidade",
    ]
    rows = []
    for a in anomalies:
        rows.append({
            "Chave NFe": a.get("doc_key", ""),
            "Filial":    a.get("branch", ""),
            "Campo Auditado": a.get("field", ""),
            "Valor Protheus (SF1/SD1)": a.get("protheus_value", ""),
            "Valor XML (SDS/SDT)":      a.get("xml_value", ""),
            "Severidade": (a.get("severity") or "").upper(),
        })
    # Se nao houver anomalia, gera planilha so com header + linha de resumo
    if not rows:
        rows = [{
            "Chave NFe": "(sem divergencias)",
            "Filial":    "—",
            "Campo Auditado": f"Auditoria OK em {stats.get('period','')}",
            "Valor Protheus (SF1/SD1)": f"{stats.get('docs_audited', 0)} documentos auditados",
            "Valor XML (SDS/SDT)": "—",
            "Severidade": "OK",
        }]
    return build_formatted_xlsx_bytes(
        rows, sheet_name="Anomalias", columns=cols, reorder=False,
    )


# ---- Entrypoint -----------------------------------------------------------

def _fiscal_notify_enabled() -> bool:
    """Master switch do e-mail do Auditor (AppSetting FISCAL_NOTIFY_ENABLED).
    Default LIGADO — desliga so quando setado explicitamente p/ 0/false/nao/off."""
    v = settings_store.get_setting("FISCAL_NOTIFY_ENABLED", "1")
    return str(v).strip().lower() not in ("0", "false", "nao", "não", "no", "off")


def _fiscal_should_email(stats: dict, autonomous_mode: bool, notify_email) -> bool:
    """Decide o envio do e-mail de conclusao:
      - master desligado           -> NUNCA envia.
      - auditoria AGENDADA (auto)  -> envia (o e-mail "na agenda").
      - auditoria MANUAL           -> so se a pessoa pediu (checkbox) E houve
                                       divergencia (nao spamar auditoria limpa)."""
    if not _fiscal_notify_enabled():
        return False
    if autonomous_mode:
        return True
    return bool(notify_email) and stats.get("anomalies", 0) > 0


def run_audit(
    *,
    date_from: date,
    date_to: date,
    branches: list[str],
    chave_filter: Optional[str] = None,
    doc_models: Optional[set[str]] = None,
    job_id: Optional[str] = None,
    progress_cb=None,
    cancel_cb=None,
    autonomous_mode: bool = False,
    notify_email: Optional[bool] = None,
    engine_kind: str = "internal",
) -> dict:
    """Roda a auditoria interna e retorna estatisticas.

    Args:
        date_from / date_to:  janela de DS_EMISSAO.
        branches:             lista de filiais.
        chave_filter:         44 digitos. Se preenchido, ignora periodo.
        doc_models:           set de modelos NFe ("55", "57", "65"). None = todos.
        autonomous_mode:      True em jobs cron — manda email mesmo com 0 anomalias.
        engine_kind:          Sprint 20 — qual motor rodar:
                              - "internal"        (default): SDS/SDT vs SF1/SD1
                              - "financeiro_se2"           : SF1 vs SE2 (Contas a Pagar)

    Returns:
        Dict com docs_audited, anomalies, critical, period, etc.
    """
    # Validacao defensiva da chave
    chave_filter = (chave_filter or "").strip() or None
    if chave_filter and (len(chave_filter) != 44 or not chave_filter.isdigit()):
        raise ValueError(
            f"chave_filter invalida (44 digitos esperados): {chave_filter!r}"
        )
    doc_models = set(doc_models) if doc_models else None

    # Sprint 20 — roteia para o motor financeiro quando solicitado.
    # Mantemos o motor padrao (internal) inline neste arquivo; o motor
    # financeiro vive em finance_audit.py mas reusa este orquestrador
    # para persistencia (FiscalAnomaly) e e-mail (executive HTML + XLSX).
    if engine_kind == "financeiro_se2":
        return _run_finance_audit(
            date_from=date_from, date_to=date_to, branches=branches,
            job_id=job_id, progress_cb=progress_cb, cancel_cb=cancel_cb,
            autonomous_mode=autonomous_mode, notify_email=notify_email,
        )
    if engine_kind == "comercial_sc5_se1":
        return _run_commercial_audit(
            date_from=date_from, date_to=date_to, branches=branches,
            job_id=job_id, progress_cb=progress_cb, cancel_cb=cancel_cb,
            autonomous_mode=autonomous_mode, notify_email=notify_email,
        )

    stats: dict = {
        "docs_audited": 0,
        "docs_skipped": 0,
        "nota_ausente": 0,           # SDS sem SF1 — categoria propria
        "anomalies": 0,              # total de divergencias gravadas
        "critical": 0,
        "ncm_divergences": 0,
        "period": f"{date_from:%d/%m/%Y} a {date_to:%d/%m/%Y}",
        "branches": branches,
        "filters": {
            "chave_filter": chave_filter,
            "doc_models": sorted(doc_models) if doc_models else None,
        },
    }

    engine = protheus_api.engine_registry.get()
    db = SessionLocal()
    anomalies_buffer: list[dict] = []

    # Ponto 3 (2026) — substitui resultados anteriores do mesmo escopo.
    stats["replaced_prior"] = _purge_prior_anomalies(
        db, branches, "internal", chave_filter,
    )

    try:
        for branch in branches:
            if cancel_cb and cancel_cb():
                stats["canceled"] = True
                break

            docs = _load_audit_period_internal(
                engine, branch, date_from, date_to,
                chave_filter=chave_filter,
            )
            logger.info(
                "Fiscal %s: %d docs carregados (SDS=%d com SF1, %d sem)",
                branch, len(docs),
                sum(1 for d in docs if d.get("sf1")),
                sum(1 for d in docs if not d.get("sf1")),
            )

            for doc in docs:
                if cancel_cb and cancel_cb():
                    stats["canceled"] = True
                    break

                chave = (doc.get("chave") or "").strip()

                # Filtro por modelo (chave[20:22] = 55, 57, 65, ...). Se nao
                # houver chave valida, ignora o filtro e audita assim mesmo.
                if doc_models and len(chave) == 44 and chave[20:22] not in doc_models:
                    stats["docs_skipped"] += 1
                    continue

                stats["docs_audited"] += 1

                rule_engine = FiscalRuleEngine(doc)
                # Sprint 13: engine retorna RELATORIO COMPLETO (ok+divergent+skipped).
                rule_engine.run()
                divs = [r for r in rule_engine.report if r.get("status") == "divergent"]
                stats["fields_checked"] = stats.get("fields_checked", 0) + len(rule_engine.report)

                # Fornecedor: prefere DS_CNPJ (XML); fallback F1_FORNECE+F1_LOJA
                sds = doc.get("sds") or {}
                sf1 = doc.get("sf1") or {}
                supplier = None
                if sds.get("DS_CNPJ"):
                    supplier = str(sds.get("DS_CNPJ") or "").strip()
                elif sf1.get("F1_FORNECE"):
                    supplier = f"{str(sf1.get('F1_FORNECE') or '').strip()}/{str(sf1.get('F1_LOJA') or '').strip()}"
                dk = _doc_key(doc)

                # Fix #4 — registra TODO documento auditado. Sem divergencia =>
                # marca 'ok' (o analista revisa todos; o toggle filtra).
                if not divs:
                    _save_doc_ok(db, dk, branch, supplier, job_id, commit=False)
                    stats["docs_ok"] = stats.get("docs_ok", 0) + 1
                    if stats["docs_audited"] % 200 == 0:
                        db.commit()   # lote — reduz contencao do lock SQLite
                    if progress_cb and stats["docs_audited"] % 100 == 0:
                        progress_cb(stats["docs_audited"], None)
                    continue

                if doc.get("sf1") is None:
                    stats["nota_ausente"] += 1

                saved = _save_anomalies(db, dk, branch, supplier, divs, job_id, commit=False)
                stats["anomalies"] += saved
                stats["critical"] += sum(1 for d in divs if d["severity"] == "critical")
                stats["ncm_divergences"] += sum(
                    1 for d in divs if "ncm" in d["field"].lower()
                )

                for d in divs:
                    anomalies_buffer.append({
                        "doc_key": dk, "branch": branch,
                        "field": d["field"], "severity": d["severity"],
                        "protheus_value": d["protheus_value"],
                        "xml_value": d["xml_value"],
                    })

                if stats["docs_audited"] % 200 == 0:
                    db.commit()   # lote — reduz contencao do lock SQLite
                if progress_cb and stats["docs_audited"] % 100 == 0:
                    progress_cb(stats["docs_audited"], None)
        # Lote final — persiste o que sobrou do ultimo bloco (< 200 docs).
        db.commit()
    finally:
        db.close()

    # E-mail final — autonomo manda sempre; manual so se houve anomalia.
    # Sprint 15: corpo curto + planilha XLSX em ANEXO (detalhamento completo).
    should_email = _fiscal_should_email(stats, autonomous_mode, notify_email)
    if should_email:
        notify = settings_store.get_setting("FISCAL_NOTIFY_EMAIL")
        if notify:
            try:
                html = _render_email_executive(stats)
                if stats["anomalies"] == 0:
                    subject = f"[Auditor Fiscal] Auditoria OK em {stats['period']}"
                    body_txt = (
                        f"Auditoria automatica concluida em {stats['period']}. "
                        f"{stats['docs_audited']} documentos auditados, ZERO divergencias. "
                        f"Planilha de confirmacao em anexo."
                    )
                else:
                    prefix = "[Auditor Fiscal AUTO]" if autonomous_mode else "[Auditor Fiscal]"
                    subject = (f"{prefix} {stats['anomalies']} divergencias "
                               f"em {stats['period']}")
                    body_txt = (
                        f"A auditoria fiscal automatica foi concluida. "
                        f"Foram auditados {stats['docs_audited']} documentos. "
                        f"Encontramos {stats['anomalies']} divergencias "
                        f"({stats['critical']} criticas, "
                        f"{stats['nota_ausente']} notas ausentes). "
                        f"Baixe a planilha em anexo para visualizar o detalhamento completo."
                    )
                # Anexo XLSX in-memory (sem tocar disco) — header verde + AutoFit
                xlsx_bytes = _build_anomalies_xlsx_bytes(anomalies_buffer, stats)
                attachment = {
                    "filename": f"auditoria_fiscal_{now_brt().strftime('%Y%m%d_%H%M')}.xlsx",
                    "content": xlsx_bytes,
                    "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                }
                send_email(
                    to=[e.strip() for e in notify.split(",") if e.strip()],
                    subject=subject, body=body_txt, html=html,
                    attachments=[attachment],
                )
                stats["email_sent_to"] = notify
                stats["autonomous_email"] = autonomous_mode
                stats["email_attachment_size"] = len(xlsx_bytes)
            except Exception as exc:
                logger.exception("Falha ao enviar e-mail de anomalias")
                stats["email_error"] = str(exc)

    return stats


# ============================================================
#  Sprint 20 — Motor Financeiro (SF1 x SE2)
# ============================================================

def _run_finance_audit(
    *, date_from: date, date_to: date, branches: list[str],
    job_id: Optional[str], progress_cb, cancel_cb, autonomous_mode: bool,
    notify_email: Optional[bool] = None,
) -> dict:
    """Pipeline do motor financeiro — segue a mesma arquitetura do interno
    mas usa `finance_audit.run_finance_audit_for_branch()`.
    Persiste em FiscalAnomaly com `field_compared` prefixado `fin_`.
    """
    from .finance_audit import run_finance_audit_for_branch

    stats: dict = {
        "engine": "financeiro_se2",
        "docs_audited": 0,
        "anomalies": 0,
        "critical": 0,
        "warn": 0,
        "ausencia_titulo": 0,
        "period": f"{date_from:%d/%m/%Y} a {date_to:%d/%m/%Y}",
        "branches": branches,
    }
    engine = protheus_api.engine_registry.get()
    db = SessionLocal()
    anomalies_buffer: list[dict] = []
    # Ponto 3 (2026) — substitui resultados financeiros anteriores do mesmo escopo.
    stats["replaced_prior"] = _purge_prior_anomalies(db, branches, "financeiro_se2")
    try:
        for branch in branches:
            if cancel_cb and cancel_cb():
                stats["canceled"] = True
                break
            docs, anomalies = run_finance_audit_for_branch(
                engine, branch, date_from, date_to,
            )
            stats["docs_audited"] += len(docs)
            if not anomalies:
                if progress_cb:
                    progress_cb(stats["docs_audited"], None)
                continue

            stats["ausencia_titulo"] += sum(
                1 for a in anomalies if a["field"] == "fin_ausencia_titulo"
            )
            stats["critical"] += sum(1 for a in anomalies if a["severity"] == "critical")
            stats["warn"] += sum(1 for a in anomalies if a["severity"] == "warn")

            # Persiste em FiscalAnomaly (reusa a tabela existente).
            for a in anomalies:
                fa = FiscalAnomaly(
                    doc_key=a["doc_key"], branch=a["branch"],
                    supplier_cnpj=None,                # SF1.F1_FORNECE+F1_LOJA no doc_key
                    field_compared=a["field"],
                    protheus_value=str(a["protheus_value"]),
                    xml_value=str(a["xml_value"]),
                    severity=a["severity"],
                    job_id=job_id,
                )
                db.add(fa)
                anomalies_buffer.append({
                    "doc_key": a["doc_key"], "branch": branch,
                    "field": a["field"], "severity": a["severity"],
                    "protheus_value": a["protheus_value"],
                    "xml_value": a["xml_value"],
                })
                stats["anomalies"] += 1
            db.commit()

            if progress_cb:
                progress_cb(stats["docs_audited"], None)
    finally:
        db.close()

    # E-mail final (reusa template executivo + anexo XLSX)
    should_email = _fiscal_should_email(stats, autonomous_mode, notify_email)
    if should_email:
        notify = settings_store.get_setting("FISCAL_NOTIFY_EMAIL")
        if notify:
            try:
                # Garante campos esperados pelo render executivo
                stats.setdefault("ncm_divergences", 0)
                stats.setdefault("nota_ausente", 0)
                html = _render_email_executive(stats)
                if stats["anomalies"] == 0:
                    subject = f"[Auditor Financeiro] Conformidade OK em {stats['period']}"
                    body_txt = (
                        f"Motor Financeiro (SF1 x SE2) concluiu em {stats['period']}. "
                        f"{stats['docs_audited']} notas auditadas, ZERO divergencias. "
                        f"Planilha de confirmacao em anexo."
                    )
                else:
                    prefix = "[Auditor Financeiro AUTO]" if autonomous_mode else "[Auditor Financeiro]"
                    subject = (f"{prefix} {stats['anomalies']} divergencias "
                               f"em {stats['period']}")
                    body_txt = (
                        f"Motor Financeiro (SF1 x SE2) detectou "
                        f"{stats['anomalies']} divergencias "
                        f"({stats['critical']} criticas, "
                        f"{stats['ausencia_titulo']} ausencias de titulo). "
                        f"Baixe a planilha em anexo para o detalhamento."
                    )
                xlsx_bytes = _build_anomalies_xlsx_bytes(anomalies_buffer, stats)
                attachment = {
                    "filename": f"auditoria_financeira_{now_brt().strftime('%Y%m%d_%H%M')}.xlsx",
                    "content": xlsx_bytes,
                    "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                }
                send_email(
                    to=[e.strip() for e in notify.split(",") if e.strip()],
                    subject=subject, body=body_txt, html=html,
                    attachments=[attachment],
                )
                stats["email_sent_to"] = notify
                stats["autonomous_email"] = autonomous_mode
            except Exception as exc:
                logger.exception("Falha ao enviar e-mail financeiro")
                stats["email_error"] = str(exc)

    return stats


# ============================================================
#  Sprint 21 — Motor Comercial (SC5 x SE1)
# ============================================================

def _run_commercial_audit(
    *, date_from: date, date_to: date, branches: list[str],
    job_id: Optional[str], progress_cb, cancel_cb, autonomous_mode: bool,
    notify_email: Optional[bool] = None,
) -> dict:
    """Pipeline do motor COMERCIAL — espelho de `_run_finance_audit` para
    `SC5 x SE1`. Persiste em FiscalAnomaly com `field_compared` prefixado `com_`.
    """
    from .commercial_audit import run_commercial_audit_for_branch

    stats: dict = {
        "engine": "comercial_sc5_se1",
        "docs_audited": 0,
        "anomalies": 0,
        "critical": 0,
        "warn": 0,
        "ausencia_titulo": 0,
        "fraude_vendedor": 0,
        "period": f"{date_from:%d/%m/%Y} a {date_to:%d/%m/%Y}",
        "branches": branches,
    }
    engine = protheus_api.engine_registry.get()
    db = SessionLocal()
    anomalies_buffer: list[dict] = []
    # Ponto 3 (2026) — substitui resultados comerciais anteriores do mesmo escopo.
    stats["replaced_prior"] = _purge_prior_anomalies(db, branches, "comercial_sc5_se1")
    try:
        for branch in branches:
            if cancel_cb and cancel_cb():
                stats["canceled"] = True
                break
            docs, anomalies = run_commercial_audit_for_branch(
                engine, branch, date_from, date_to,
            )
            stats["docs_audited"] += len(docs)
            if not anomalies:
                if progress_cb:
                    progress_cb(stats["docs_audited"], None)
                continue

            stats["ausencia_titulo"] += sum(
                1 for a in anomalies if a["field"] == "com_ausencia_titulo"
            )
            stats["fraude_vendedor"] += sum(
                1 for a in anomalies if a["field"] == "com_fraude_vendedor"
            )
            stats["critical"] += sum(1 for a in anomalies if a["severity"] == "critical")
            stats["warn"] += sum(1 for a in anomalies if a["severity"] == "warn")

            for a in anomalies:
                fa = FiscalAnomaly(
                    doc_key=a["doc_key"], branch=a["branch"],
                    supplier_cnpj=None,  # SC5 nao tem CNPJ direto — fica null
                    field_compared=a["field"],
                    protheus_value=str(a["protheus_value"]),
                    xml_value=str(a["xml_value"]),
                    severity=a["severity"],
                    job_id=job_id,
                )
                db.add(fa)
                anomalies_buffer.append({
                    "doc_key": a["doc_key"], "branch": branch,
                    "field": a["field"], "severity": a["severity"],
                    "protheus_value": a["protheus_value"],
                    "xml_value": a["xml_value"],
                })
                stats["anomalies"] += 1
            db.commit()

            if progress_cb:
                progress_cb(stats["docs_audited"], None)
    finally:
        db.close()

    # E-mail final (reusa template executivo + anexo XLSX)
    should_email = _fiscal_should_email(stats, autonomous_mode, notify_email)
    if should_email:
        notify = settings_store.get_setting("FISCAL_NOTIFY_EMAIL")
        if notify:
            try:
                stats.setdefault("ncm_divergences", 0)
                stats.setdefault("nota_ausente", 0)
                html = _render_email_executive(stats)
                if stats["anomalies"] == 0:
                    subject = f"[Auditor Comercial] Conformidade OK em {stats['period']}"
                    body_txt = (
                        f"Motor Comercial (SC5 x SE1) concluiu em {stats['period']}. "
                        f"{stats['docs_audited']} pedidos faturados, ZERO divergencias."
                    )
                else:
                    prefix = "[Auditor Comercial AUTO]" if autonomous_mode else "[Auditor Comercial]"
                    subject = (f"{prefix} {stats['anomalies']} divergencias "
                               f"em {stats['period']}")
                    body_txt = (
                        f"Motor Comercial (SC5 x SE1) detectou "
                        f"{stats['anomalies']} divergencias "
                        f"({stats['critical']} criticas, "
                        f"{stats['ausencia_titulo']} ausencias de titulo, "
                        f"{stats['fraude_vendedor']} divergencias de vendedor). "
                        f"Baixe a planilha em anexo para o detalhamento."
                    )
                xlsx_bytes = _build_anomalies_xlsx_bytes(anomalies_buffer, stats)
                attachment = {
                    "filename": f"auditoria_comercial_{now_brt().strftime('%Y%m%d_%H%M')}.xlsx",
                    "content": xlsx_bytes,
                    "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                }
                send_email(
                    to=[e.strip() for e in notify.split(",") if e.strip()],
                    subject=subject, body=body_txt, html=html,
                    attachments=[attachment],
                )
                stats["email_sent_to"] = notify
                stats["autonomous_email"] = autonomous_mode
            except Exception as exc:
                logger.exception("Falha ao enviar e-mail comercial")
                stats["email_error"] = str(exc)

    return stats

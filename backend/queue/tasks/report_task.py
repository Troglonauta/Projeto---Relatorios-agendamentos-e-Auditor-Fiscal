"""Task Celery — gera relatorio em streaming.

Suporta DOIS modos (Sprint 5):
1. **Single-table**: payload `{table, columns, filters}` — usa `protheus_api`.
2. **Multi-table (JOIN)**: payload `{alias, branch, joins[], columns, filters}`
   — usa `JoinQueryBuilder` para SQL + paginacao + qualificacao de colunas.

Headers da planilha refletem:
- Single: nome puro da coluna (`C5_NUM`).
- Multi : nome qualificado (`SC5__C5_NUM`, `SA1__A1_NOME`).

Roteiro comum:
1. Carrega Job, marca running.
2. Conta linhas total (COUNT_BIG) para progresso.
3. Itera em chunks via pandas (`chunksize=10000`).
4. Grava `.tmp` -> rename atomico ao final.
5. Mark done / failed / canceled.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from celery.signals import worker_ready

from ... import jobs as jobs_mod
from ... import protheus_api
from ...config import get_settings
from ...protheus_api import ProtheusError
from ...reports import (
    JobCanceled, RowLimitExceeded,
    write_csv_stream, write_xlsx_stream,
)
from ..celery_app import celery_app

logger = logging.getLogger(__name__)
settings = get_settings()
OUTPUT_DIR = Path(settings.REPORTS_OUTPUT_DIR)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@worker_ready.connect
def _on_worker_ready(**_):
    try:
        n = jobs_mod.reset_orphan_jobs()
        if n:
            logger.warning("Marquei %d job(s) orfao(s) como failed (ERR-JOB-002).", n)
    except Exception:
        logger.exception("Falha em reset_orphan_jobs")


# ============================================================
#  Helpers de query — single ou JOIN
# ============================================================

def _build_single_query(payload: dict) -> tuple[str, str, dict, str]:
    """Constroi (data_sql, count_sql, params, output_name) para single-table."""
    table = payload["table"]
    columns = payload.get("columns") or None
    filters = payload.get("filters") or None

    filter_sql, params = protheus_api._build_filter_clause(filters or {})
    where = "D_E_L_E_T_ = ' '"
    if filter_sql:
        where = f"{where} AND {filter_sql}"
    safe_table = protheus_api._safe_ident(table, "Tabela")

    if columns:
        validated_cols = [protheus_api._safe_ident(c, "Coluna") for c in columns]
        cols_sql = ", ".join(validated_cols)
    else:
        cols_sql = "*"

    data_sql = (
        f"SELECT {cols_sql} FROM {safe_table} WITH (NOLOCK) "
        f"WHERE {where} ORDER BY R_E_C_N_O_"
    )
    count_sql = f"SELECT COUNT_BIG(1) FROM {safe_table} WITH (NOLOCK) WHERE {where}"
    return data_sql, count_sql, params, safe_table


def _build_join_query(payload: dict) -> tuple[str, str, dict, str]:
    """Constroi (data_sql_sem_pag, count_sql, params, output_name) para JOIN.

    A query do JoinQueryBuilder vem com OFFSET/FETCH; aqui o worker ignora
    paginacao e itera com `pd.read_sql(chunksize=...)`. Removemos o trecho
    `OFFSET... FETCH...` no SQL antes de passar para o pandas.
    """
    from ...query_engine import JoinCond, JoinQueryBuilder, JoinSpec

    alias = payload["alias"].upper()
    branch = payload["branch"]
    columns = payload.get("columns") or None
    filters = payload.get("filters") or None
    joins_payload = payload.get("joins") or []

    join_specs = [
        JoinSpec(
            alias=j["alias"].upper(), branch=j["branch"],
            join_type=(j.get("join_type") or "INNER").upper(),
            on=[JoinCond(c["left_alias"].upper(), c["left_column"].upper(),
                         c["right_column"].upper()) for c in j.get("on", [])],
        )
        for j in joins_payload
    ]

    suffix = settings.PROTHEUS_TABLE_SUFFIX if hasattr(settings, "PROTHEUS_TABLE_SUFFIX") else "0"
    b = JoinQueryBuilder(
        base=(alias, branch), joins=join_specs,
        columns=columns, filters=filters, table_suffix=suffix,
    )
    data_sql, count_sql, params, _output = b.build(page=1, page_size=1_000_000_000)
    # Tira OFFSET/FETCH — pandas usa chunksize
    if "OFFSET" in data_sql:
        data_sql = data_sql.split("ORDER BY")[0] + "ORDER BY t1.R_E_C_N_O_"
    # Remove _offset e _limit dos params (so do data_sql)
    params.pop("_offset", None); params.pop("_limit", None)

    name_hint = f"{alias}_join{len(join_specs)}"
    return data_sql, count_sql, params, name_hint


# ============================================================
#  Task principal
# ============================================================

@celery_app.task(name="reports.generate_report", bind=True)
def generate_report(self, job_id: str) -> dict:
    """Processa um job de relatorio (single OU JOIN). Retorna resumo."""
    job = jobs_mod.get_job(job_id)
    if not job:
        return {"ok": False, "reason": "job_not_found"}

    jobs_mod.mark_running(job_id, celery_task_id=self.request.id)

    try:
        payload = json.loads(job.payload_json)
        file_format = (payload.get("file_format") or "csv").lower()
        if file_format not in ("xlsx", "csv"):
            jobs_mod.mark_failed(job_id, "ERR-JOB-006",
                                 f"Formato '{file_format}' nao suportado (use xlsx/csv)")
            return {"ok": False}

        has_joins = bool(payload.get("joins"))
        if has_joins:
            data_sql, count_sql, params, name_hint = _build_join_query(payload)
            logger.info("Job %s: modo JOIN (%d joins)", job_id, len(payload["joins"]))
        else:
            data_sql, count_sql, params, name_hint = _build_single_query(payload)
            logger.info("Job %s: modo single-table (%s)", job_id, name_hint)

        from sqlalchemy import text
        engine = protheus_api.engine_registry.get()

        # 1) Conta linhas
        with engine.connect() as conn:
            rows_total = int(conn.execute(text(count_sql), params).scalar() or 0)
        jobs_mod.update_progress(job_id, 0, rows_total=rows_total)

        # 2) Stream
        out_name = f"{name_hint}_{job_id[:8]}.{file_format}"
        final_path = OUTPUT_DIR / out_name
        tmp_path = OUTPUT_DIR / (out_name + ".tmp")

        def _progress(rows_so_far):
            jobs_mod.update_progress(job_id, rows_so_far, rows_total=rows_total)

        def _cancel():
            return jobs_mod.is_cancel_requested(job_id)

        import pandas as pd

        def _iter_rows():
            with engine.connect() as conn:
                for chunk in pd.read_sql(text(data_sql), conn, params=params, chunksize=10000):
                    for r in chunk.to_dict(orient="records"):
                        # JOIN: chaves ja vem como SC5__C5_NUM (refletem no header)
                        # Single: chaves sao nomes puros C5_NUM
                        if has_joins:
                            # Nao mexer nos nomes — manter o qualificado para o cabecalho
                            yield r
                        else:
                            yield protheus_api._normalize_row(r)

        # Sprint 8 Part 3 — LGPD: se o dono do job NAO for admin, embrulha
        # o iterator com a mascara. Streaming preservado (O(1) de memoria).
        from ... import lgpd
        from ...models import User
        from ...database import SessionLocal
        owner_user = None
        if job.owner_id:
            _db = SessionLocal()
            try:
                owner_user = _db.query(User).filter(User.id == job.owner_id).first()
            finally:
                _db.close()
        row_source = _iter_rows()
        if lgpd.should_mask_for(owner_user):
            row_source = lgpd.wrap_row_iterator(row_source, owner_user)

        # O worker Celery NAO roda o lifespan do FastAPI, entao garante o
        # dicionario SX3 carregado (idempotente) para humanizar os cabecalhos.
        try:
            from ... import dict_sx3
            dict_sx3.load_sx3()
        except Exception:
            pass

        # Nome fisico das colunas no export so para admin (operador ve so titulo).
        _admin = bool(owner_user and getattr(owner_user, "role", "") == "admin")
        if file_format == "xlsx":
            rows_written = write_xlsx_stream(
                tmp_path, row_source, _progress, _cancel, include_physical=_admin)
        else:
            rows_written = write_csv_stream(
                tmp_path, row_source, _progress, _cancel, include_physical=_admin)

        # 3) Rename atomico
        os.replace(tmp_path, final_path)
        jobs_mod.mark_done(
            job_id, file_path=str(final_path),
            file_size=final_path.stat().st_size, rows=rows_written,
        )
        logger.info("Job %s done: %d linhas em %s", job_id, rows_written, out_name)
        return {"ok": True, "rows": rows_written, "path": str(final_path)}

    except JobCanceled:
        jobs_mod.mark_canceled(job_id)
        for p in OUTPUT_DIR.glob(f"*{job_id[:8]}*.tmp"):
            try: p.unlink()
            except Exception: pass
        return {"ok": False, "reason": "canceled"}

    except RowLimitExceeded as exc:
        jobs_mod.mark_failed(job_id, "ERR-JOB-004", str(exc))
        return {"ok": False, "reason": "row_limit"}

    except ProtheusError as exc:
        jobs_mod.mark_failed(job_id, "ERR-PROTHEUS-001", str(exc))
        return {"ok": False, "reason": "protheus"}

    except Exception as exc:
        logger.exception("Falha critica no job %s", job_id)
        jobs_mod.mark_failed(job_id, "ERR-JOB-005", str(exc))
        return {"ok": False, "reason": "critical"}

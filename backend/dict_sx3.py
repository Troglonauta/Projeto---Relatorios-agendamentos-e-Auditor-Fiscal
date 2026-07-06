"""Dicionário SX3 do Protheus — Sprint 6.

A tabela SX3 (Campos) tem um título humano em português (`X3_TITULO`) para
cada campo das tabelas Protheus. Em vez de exportar `C5_EMISSAO` cru, queremos
mostrar "Data de Emissão (C5_EMISSAO)" no header do Excel.

Estratégia de performance:
- Carrega TODA a SX3 em memória UMA VEZ (no boot ou primeira request) — são
  alguns milhares de linhas, ~1 MB em RAM.
- Cache thread-safe com `threading.Lock`.
- A tradução é aplicada APENAS no momento de escrever o header do XLSX —
  zero overhead por linha de dado.
- Endpoint admin `POST /api/admin/sx3/reload` força refresh quando o cliente
  customiza títulos no Configurador Protheus.

A tabela SX3 não tem filial (`X3_FILIAL` existe mas é geralmente vazia). O
nome físico padrão Fertimaxi é `SX3010` (alias + branch + sufixo), mas para
SX3 o branch é convencionalmente '0' ou vazio — tentamos algumas variações.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)

# Cache global thread-safe
_CACHE: dict[tuple[str, str], dict] = {}   # (alias, campo) → {titulo, descric}
_BY_FIELD: dict[str, dict] = {}            # campo → {titulo, descric} (fallback sem alias)
_LOCK = threading.Lock()
_LOADED = False
# Evita re-tentar o load (4 timeouts de Protheus) a CADA chamada de humanizacao
# quando a SX3 esta indisponivel. Tenta 1x por processo; reset no reload/boot.
_LOAD_TRIED = False


def _resolve_sx3_table_name() -> list[str]:
    """Candidatos para nome físico da SX3 — varia entre releases Protheus.

    Tenta na ordem: SX3010 (padrão Fertimaxi), SX3990 (instancias antigas),
    SX3000, SX3. A primeira que responder vence.
    """
    return ["SX3010", "SX3990", "SX3000", "SX3"]


def load_sx3(force: bool = False) -> dict:
    """Carrega o dicionário SX3 em memória. Idempotente.

    Retorna `{"loaded": True/False, "count": N, "source": "SX3010"}` para log.
    """
    global _LOADED, _LOAD_TRIED
    with _LOCK:
        if _LOADED and not force:
            return {"loaded": False, "count": len(_CACHE), "source": "cached"}
        _LOAD_TRIED = True   # marca tentativa (evita thrash de re-tentativas)

        # Tenta cada candidato de tabela
        from .protheus_api import engine_registry, ProtheusError
        try:
            engine = engine_registry.get()
        except Exception as exc:
            logger.warning("SX3: engine Protheus indisponivel — %s", exc)
            return {"loaded": False, "count": 0, "error": str(exc)}

        new_cache: dict[tuple[str, str], dict] = {}
        new_by_field: dict[str, dict] = {}
        loaded_from = None

        for candidate in _resolve_sx3_table_name():
            try:
                with engine.connect() as conn:
                    rs = conn.execute(text(
                        f"SELECT X3_ARQUIVO, X3_CAMPO, X3_TITULO, X3_DESCRIC "
                        f"FROM {candidate} WITH (NOLOCK) "
                        f"WHERE D_E_L_E_T_ = ' '"
                    )).all()
            except Exception as exc:
                logger.info("SX3: '%s' nao acessivel: %s", candidate, exc)
                continue

            for row in rs:
                arq = (row[0] or "").strip().upper()
                campo = (row[1] or "").strip().upper()
                titulo = (row[2] or "").strip()
                descric = (row[3] or "").strip()
                if not campo:
                    continue
                entry = {"titulo": titulo, "descric": descric, "alias": arq}
                # Indexa por (alias, campo) — chave forte (sem ambiguidade)
                if arq:
                    new_cache[(arq, campo)] = entry
                # Indexa também só por campo — fallback quando alias não está claro
                # (último vence — colunas com mesmo nome em tabelas diferentes
                # ficam com o título da última encontrada, raro mas aceitável)
                new_by_field[campo] = entry

            loaded_from = candidate
            break

        if not loaded_from:
            logger.warning("SX3: nenhuma das tabelas %s respondeu — humanização desativada",
                           _resolve_sx3_table_name())
            return {"loaded": False, "count": 0, "source": None}

        _CACHE.clear(); _CACHE.update(new_cache)
        _BY_FIELD.clear(); _BY_FIELD.update(new_by_field)
        _LOADED = True
        logger.info("SX3: %d campos carregados de '%s'", len(_CACHE), loaded_from)
        return {"loaded": True, "count": len(_CACHE), "source": loaded_from}


def _parse_column(column: str) -> tuple[Optional[str], str]:
    """Decompõe nome de coluna em (alias, campo).

    Suporta 2 formatos:
    - "SC5__C5_EMISSAO"  →  ("SC5", "C5_EMISSAO")  (modo JOIN)
    - "C5_EMISSAO"       →  (None, "C5_EMISSAO")   (single — tenta inferir do prefixo)

    Para single, o prefixo de 2 chars (ex "C5") pode ser inferido para alias
    "SC5" (consulta nas chaves do cache), mas como heurística. Se não bater,
    devolve None e usa o `_BY_FIELD` no humanize_header.
    """
    if "__" in column:
        a, _, c = column.partition("__")
        return a.upper(), c.upper()
    return None, column.upper()


def humanize_header(column: str) -> str:
    """Traduz `SC5__C5_EMISSAO` (ou `C5_EMISSAO`) para:

        - se SX3 encontrar: "Data de Emissão (C5_EMISSAO)"
        - senão devolve o nome original sem mexer

    Função BARATA — só faz lookup em dict + format de string. Pode ser chamada
    no header do Excel sem impacto perceptível (em 50 colunas = 50 lookups).
    """
    if not _LOADED and not _LOAD_TRIED:
        # Tenta lazy-load (best-effort, sem bloquear se falhar)
        try:
            load_sx3()
        except Exception:
            return column

    alias, campo = _parse_column(column)
    entry = None
    if alias:
        entry = _CACHE.get((alias, campo))
    if not entry:
        entry = _BY_FIELD.get(campo)

    if not entry or not entry.get("titulo"):
        return column

    titulo = entry["titulo"]
    return f"{titulo} ({campo})"


def humanize_title(column: str) -> str:
    """Igual a humanize_header, mas retorna SOMENTE o titulo SX3 (sem o nome
    fisico entre parenteses). Ex.: `D1_CF` -> "CFOP". Se a SX3 nao tiver, devolve
    o nome original. O nome fisico fica para tooltip (frontend)."""
    if not _LOADED and not _LOAD_TRIED:
        try:
            load_sx3()
        except Exception:
            return column
    alias, campo = _parse_column(column)
    entry = None
    if alias:
        entry = _CACHE.get((alias, campo))
    if not entry:
        entry = _BY_FIELD.get(campo)
    if entry and entry.get("titulo"):
        return entry["titulo"]
    return column


def get_field_info(column: str) -> Optional[dict]:
    """Retorna o dict bruto `{titulo, descric, alias}` ou None se não houver.

    Útil para tooltips no frontend (descric é mais longa que titulo).
    """
    if not _LOADED and not _LOAD_TRIED:
        try: load_sx3()
        except Exception: return None
    alias, campo = _parse_column(column)
    entry = None
    if alias:
        entry = _CACHE.get((alias, campo))
    if not entry:
        entry = _BY_FIELD.get(campo)
    return entry


def stats() -> dict:
    """Stats para debug/admin: total de campos, exemplos."""
    with _LOCK:
        return {
            "loaded": _LOADED,
            "total_fields": len(_CACHE),
            "total_by_field": len(_BY_FIELD),
            "sample": [
                {"alias": a, "campo": c, "titulo": v.get("titulo")}
                for (a, c), v in list(_CACHE.items())[:5]
            ],
        }

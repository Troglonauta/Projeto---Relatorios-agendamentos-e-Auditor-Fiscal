"""Versao da aplicacao + data da build.

Atualizar a cada release. O frontend exibe esses valores no rodape do sidebar
(`layout.js`) e a tela de Administracao usa para identificar a build em logs.
"""
from __future__ import annotations

# === Versao e build ===========================================================
# Politica de versionamento (SemVer):
#   MAJOR — quebras de API/schema (ex: migracao destrutiva de banco)
#   MINOR — novas funcionalidades retrocompativeis
#   PATCH — correcoes (bugfix, ajustes UX)
# Atualize JUNTO com `BUILD_DATE` a cada deploy.
VERSION    = "2.29.5"     # Auditoria nao morre mais por lock de UPDATE de progresso: _patch faz retry e ENGOLE lock (nunca derruba o job). Fork-safety Celery: worker_process_init -> engine.dispose() garante PRAGMAs (busy_timeout=30s) nas conexoes forkadas (era instantaneo o "database is locked" no worker)
BUILD_DATE = "2026-07-13" # ISO YYYY-MM-DD
PHASE      = "v2.29"      # rotulo exibido na UI ("v2.29 · v2.29.5")

# Codename interno do release — opcional, aparece em /api/health
CODENAME = "internal-audit"


def version_info() -> dict:
    """Dict serializavel — usado por /api/health e /api/settings/public."""
    return {
        "version": VERSION,
        "build_date": BUILD_DATE,
        "phase": PHASE,
        "codename": CODENAME,
    }

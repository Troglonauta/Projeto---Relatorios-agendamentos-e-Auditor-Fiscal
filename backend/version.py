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
VERSION    = "2.29.3"     # 3 fixes: (1) snapshot do Status em BRT (era UTC); (2) operador fiscal agora acompanha/cancela a auditoria (jobs GET/DELETE aceitam 'fiscal' via require_any_action) — antes 403 travava; (3) menu Consultas gateado por 'view' + landing manda analista fiscal pro Auditor
BUILD_DATE = "2026-07-13" # ISO YYYY-MM-DD
PHASE      = "v2.29"      # rotulo exibido na UI ("v2.29 · v2.29.3")

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

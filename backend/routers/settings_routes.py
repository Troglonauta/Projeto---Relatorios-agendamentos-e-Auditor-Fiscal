"""Settings publicos consumidos pelo frontend (sem autenticacao).

Expoe APENAS valores nao-sensiveis: timezone, janela de inatividade,
branding (nome, logo, cor), filiais da operacao, versao do app.
"""
from fastapi import APIRouter

from ..config import get_settings
from ..security import settings_store
from ..version import BUILD_DATE, VERSION

router = APIRouter(prefix="/api/settings", tags=["settings"])
_settings = get_settings()

DEFAULT_BRANCHES = "01,02,03,04,05,06,07,08"


def _public(key: str, default):
    v = settings_store.get_setting(key)
    return v if v not in (None, "") else default


def _branches_list() -> list[str]:
    """Lista FIXA de filiais da operacao Fertimaxi. Editavel via Admin.

    Default '01..08' cobre o cenario atual. Quando uma filial nova entra em
    operacao, o admin adiciona aqui — independe do que existe nas tabelas.
    """
    csv = _public("BRANCHES_LIST", DEFAULT_BRANCHES)
    return [b.strip() for b in str(csv).split(",") if b.strip()]


@router.get("/public")
def public_settings():
    return {
        "app_name": _public("APP_NAME", _settings.APP_NAME),
        "version": VERSION,
        "build_date": BUILD_DATE,
        "timezone": _public("APP_TIMEZONE", _settings.APP_TIMEZONE),
        "timezone_label": "BRT",  # Horario de Brasilia
        "idle_minutes": int(_public("SESSION_IDLE_MINUTES", _settings.SESSION_IDLE_MINUTES)),
        "max_concurrent_sessions": int(_public("MAX_CONCURRENT_SESSIONS", 3)),
        "table_suffix": _public("PROTHEUS_TABLE_SUFFIX", _settings.PROTHEUS_TABLE_SUFFIX),
        "branches": _branches_list(),
        "setup_complete": settings_store.setup_complete(),
        "branding": {
            "app_name": _public("APP_NAME", _settings.APP_NAME),
            "logo_url": "/api/branding/logo",
            "primary_color": _public("PRIMARY_COLOR", "#2E8B3D"),
        },
    }

"""Sprint 8 — helpers de timezone para America/Sao_Paulo (BRT).

A app inteira historicamente usa `datetime.utcnow()` (naive UTC). Para
features novas onde a UI mostra horario direto ao operador (auditoria fiscal,
ack/snooze, exports), trabalhamos em BRT para evitar o gap de -3h.

Use `now_brt()` em vez de `datetime.utcnow()` quando o valor for renderizado
para humanos sem conversao adicional. Para timestamps internos (JWT exp,
sessoes, auditoria tecnica) mantemos UTC — nao mexer aqui.

Trade-off: armazenar naive-BRT no SQLite e' OK enquanto a app rodar num
unico fuso (Brasilia). Se algum dia rodar multi-region, migrar para
`DateTime(timezone=True)` + UTC aware no banco.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# Python 3.9+ (Windows precisa do pacote `tzdata` para esses nomes de tz)
try:
    from zoneinfo import ZoneInfo
    BRT_TZ = ZoneInfo("America/Sao_Paulo")
    _HAS_ZONEINFO = True
except Exception:
    # Fallback estatico (UTC-3 sem DST) — vale para Brasilia desde 2019,
    # data em que o Brasil aboliu horario de verao por decreto.
    BRT_TZ = timezone(timedelta(hours=-3), name="America/Sao_Paulo")
    _HAS_ZONEINFO = False


def now_brt() -> datetime:
    """Retorna `datetime.now()` em BRT, **naive** (sem tzinfo).

    Naive porque os Columns DateTime() do projeto sao naive — armazenar
    aware criaria offset duplicado quando o SQLAlchemy serializasse.

    Quando a app for migrada para DateTime(timezone=True), trocar por
    `datetime.now(BRT_TZ)` (aware) sem precisar atualizar callers.
    """
    return datetime.now(BRT_TZ).replace(tzinfo=None)


def aware_brt(dt: datetime) -> datetime:
    """Converte um naive-BRT para aware-BRT (uso em formatadores)."""
    if dt.tzinfo is not None:
        return dt.astimezone(BRT_TZ)
    return dt.replace(tzinfo=BRT_TZ)

"""Helpers de comparacao — Sprint 12.

Apos a migracao para auditoria 100% interna (SDS/SDT vs SF1/SD1), os
comparadores antigos (XML × Protheus, SDT vs xml_items, SE2 vs cobr/dup
do XML, etc.) saem da arvore — o `rule_engine.py` faz tudo internamente
em ~10 metodos auto-contidos.

Este modulo passa a expor APENAS:
- `Divergence` (dataclass — schema serializavel)
- Helpers de tolerancia (`tol_valor`, `tol_icms`, `tol_qtd`) lidos de
  `AppSetting` com fallback.
- Normalizacao basica (`_norm_digits`, `_norm_decimal`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional


Severity = str  # "info" | "warn" | "critical"


@dataclass
class Divergence:
    field: str
    protheus_value: str
    xml_value: str
    severity: Severity
    note: str = ""


# ---- Tolerancias configuraveis ---------------------------------------------

def _tol(key: str, default: Decimal) -> Decimal:
    """Le tolerancia do settings_store; cai no default se ausente/invalida."""
    try:
        from ..security import settings_store
        v = settings_store.get_setting(key)
        if v is None or v == "":
            return default
        return Decimal(str(v))
    except Exception:
        return default


def tol_valor() -> Decimal:
    """Tolerancia em R$ para totais/valores. Default R$ 0,05 (5 centavos).

    Cobre arredondamento de rateio do Protheus em frete e imposto.
    Configuravel via AppSetting `FISCAL_TOLERANCE_VALOR_RS`.
    """
    return _tol("FISCAL_TOLERANCE_VALOR_RS", Decimal("0.05"))


def tol_icms() -> Decimal:
    """Tolerancia ICMS — mais restrita (R$ 0,02)."""
    return _tol("FISCAL_TOLERANCE_ICMS_RS", Decimal("0.02"))


def tol_qtd() -> Decimal:
    """Tolerancia em quantidade (4 casas decimais — 0,01)."""
    return _tol("FISCAL_TOLERANCE_QUANT", Decimal("0.01"))


# ---- Normalizacao ----------------------------------------------------------

def _norm_digits(s) -> str:
    if s is None:
        return ""
    return re.sub(r"\D", "", str(s))


def _norm_decimal(v, places: int = 2) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    try:
        if isinstance(v, str):
            v = v.replace(",", ".").strip()
        return Decimal(str(v)).quantize(Decimal(10) ** -places)
    except (InvalidOperation, ValueError):
        return None

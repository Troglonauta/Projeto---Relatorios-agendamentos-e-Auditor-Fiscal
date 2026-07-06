"""Sprint 8 Part 3 — Mascaramento LGPD de campos sensiveis.

Aplica-se a TODOS os exports e previews do Builder (JSON, XLSX, CSV) quando
o usuario logado nao e' admin. Admin vê os dados crus para finalizacao
contabil/fiscal — operador comum recebe valores mascarados (compliance LGPD).

Catalogo de colunas sensiveis foi montado a partir do dicionario oficial
Fertimaxi (Protheus 12.1). Cobre as 4 categorias mais criticas:

| Categoria | Exemplos | Tratamento |
|---|---|---|
| CNPJ/CPF/CGC | A1_CGC, A2_CGC, F1_CGCFOR | mantem 2 primeiros + 2 ultimos digitos |
| Inscricao Estadual | A1_INSCR, A2_INSCR | totalmente mascarado |
| Salario / financeiro pessoal | RA_SALARIO, RA_VALORHORA | totalmente mascarado |
| Documento pessoal | RA_CIC (CPF RH), RA_RG, A1_RG | totalmente mascarado |

A logica reconhece colunas tanto em formato single (`A1_CGC`) quanto
qualificado por alias da Sprint 5 JOIN (`SA1__A1_CGC`).

Performance: o cache compila o set de colunas sensiveis das `headers`
UMA vez por export — depois o loop e' so dict lookup O(1) por linha.
"""
from __future__ import annotations

import logging
from typing import Iterable, Iterator, Optional

logger = logging.getLogger(__name__)


# Mascara padronizada para valores totalmente proibidos (salario, RG).
MASK_FULL = "*** LGPD ***"


# ---- Catalogo de colunas sensiveis (suffix da coluna fisica Protheus) -----
#
# Chave: nome curto (sem prefixo de alias). Valor: tipo de mascara.
#  - "doc_partial": mantem 2 primeiros + 2 ultimos digitos (CNPJ/CPF/CGC)
#  - "full":        substitui por MASK_FULL (salario, IE, RG, etc)

SENSITIVE_COLUMNS: dict[str, str] = {
    # CNPJ / CPF — varias tabelas Protheus
    "A1_CGC":     "doc_partial",   # Cliente
    "A2_CGC":     "doc_partial",   # Fornecedor
    "F1_CGCFOR":  "doc_partial",   # NF entrada — CGC do fornecedor (release-dep)
    "F2_CGCDEST": "doc_partial",   # NF saida — CGC do destinatario
    "RA_CIC":     "doc_partial",   # CPF do funcionario (RH)

    # Inscricao Estadual / Municipal — pode revelar atividade economica
    "A1_INSCR":    "full",
    "A2_INSCR":    "full",
    "A1_INSCRM":   "full",
    "A2_INSCRM":   "full",

    # Documento pessoal — totalmente proibido para nao-admin
    "RA_RG":       "full",
    "A1_RG":       "full",
    "A2_RG":       "full",

    # Salario / remuneracao — RH
    "RA_SALARIO":  "full",
    "RA_VALORHORA":"full",
    "RA_HRSEMAN":  "full",   # horas semanais (pode revelar regime)

    # Contato pessoal (LGPD art. 5 — dado pessoal)
    "A1_PESSOAL":  "full",
    "A2_PESSOAL":  "full",
}


# ---- Detector: coluna sensivel? -------------------------------------------

def _column_suffix(col_name: str) -> str:
    """Sprint 5 JOIN qualifica colunas como `SA1__A1_CGC`. Retorna o sufixo
    `A1_CGC` para casar com o catalogo. Single mode (`A1_CGC`) retorna o
    proprio nome."""
    if "__" in col_name:
        return col_name.split("__", 1)[1].upper()
    return col_name.upper()


def _classify(col_name: str) -> Optional[str]:
    """Retorna o tipo de mascara ('doc_partial' / 'full') ou None se a coluna
    nao for sensivel."""
    suffix = _column_suffix(col_name)
    return SENSITIVE_COLUMNS.get(suffix)


# ---- Mascaras ---------------------------------------------------------------

def _mask_doc_partial(value) -> str:
    """CNPJ/CPF/CGC: `12345678901234` -> `12.***.***/****-34`.

    Mantem 2 primeiros + 2 ultimos digitos para permitir reconciliacao
    parcial (operador consegue cruzar com extratos sem ver o doc completo).
    Strings sem digitos suficientes viram MASK_FULL.
    """
    if value is None or value == "":
        return value
    s = str(value)
    digits = "".join(c for c in s if c.isdigit())
    if len(digits) < 5:
        return MASK_FULL
    if len(digits) == 11:
        # CPF: 12345678901 -> 12.***.***-01
        return f"{digits[:2]}.***.***-{digits[-2:]}"
    # CNPJ ou variante (mantem 2 primeiros + 2 ultimos)
    return f"{digits[:2]}.***.***/****-{digits[-2:]}"


# ---- API publica ------------------------------------------------------------

def should_mask_for(user) -> bool:
    """True quando o usuario NAO e' admin (operador comum vê dados mascarados).

    Aceita objeto User do SQLAlchemy ou string com o role.
    """
    if user is None:
        return True
    role = getattr(user, "role", None) or (user if isinstance(user, str) else None)
    return str(role).lower() != "admin"


def build_mask_map(columns: Iterable[str]) -> dict[str, str]:
    """Pre-calcula `{col_name: mask_type}` para as colunas presentes. Retorna
    dict vazio quando nada e' sensivel — caller pode pular o loop inteiro.
    """
    out: dict[str, str] = {}
    for c in columns:
        kind = _classify(c)
        if kind:
            out[c] = kind
    return out


def mask_value(value, mask_kind: str):
    """Aplica a mascara segundo o tipo. None passa direto (preserva nulos)."""
    if mask_kind == "doc_partial":
        return _mask_doc_partial(value)
    return MASK_FULL


def apply_to_rows(rows: list[dict], user) -> list[dict]:
    """Mascara in-place + retorna a mesma lista. NAO chame se `user` e' admin
    (overhead desnecessario) — use `should_mask_for(user)` antes.

    Mutacao in-place evita criar copias para datasets grandes.
    """
    if not rows:
        return rows
    mask_map = build_mask_map(rows[0].keys())
    if not mask_map:
        return rows
    for r in rows:
        for col, kind in mask_map.items():
            if col in r:
                r[col] = mask_value(r[col], kind)
    return rows


def wrap_row_iterator(row_iter: Iterator[dict], user) -> Iterator[dict]:
    """Wrapper para streaming (XLSX/CSV). Aplica a mascara enquanto a linha
    sai do banco — preserva o O(1) de memoria do `write_xlsx_stream`.

    Se nao houver coluna sensivel no primeiro batch, devolve o iterator
    original (zero overhead).
    """
    if not should_mask_for(user):
        yield from row_iter
        return
    # Espia a primeira linha para descobrir as colunas
    first = next(row_iter, None)
    if first is None:
        return
    mask_map = build_mask_map(first.keys())
    if not mask_map:
        # Sem colunas sensiveis — passa tudo sem mexer
        yield first
        yield from row_iter
        return
    # Caminho com mascara
    for col, kind in mask_map.items():
        if col in first:
            first[col] = mask_value(first[col], kind)
    yield first
    logger.info(
        "LGPD: mascarando %d coluna(s) sensivel(eis) para user nao-admin: %s",
        len(mask_map), sorted(mask_map.keys()),
    )
    for row in row_iter:
        for col, kind in mask_map.items():
            if col in row:
                row[col] = mask_value(row[col], kind)
        yield row

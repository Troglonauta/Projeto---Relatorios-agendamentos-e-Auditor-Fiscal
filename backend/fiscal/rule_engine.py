"""FiscalRuleEngine — Sprint 13 (Auditoria Completa SDS/SDT vs SF1/SD1).

Mudanca de paradigma vs Sprint 12: o motor agora retorna **um item por campo
auditado**, sempre — com `status='ok'` quando bate ou `status='divergent'`
quando diverge. Campos onde um dos lados nao tem dado viram `status='skipped'`.

Estrutura de cada item retornado:

    {
      "field":          "valor_total",          # chave tecnica
      "label":          "Valor Total",           # rotulo amigavel p/ UI
      "category":       "header" | "item",       # agrupamento
      "item_n":         None | "1" | "2",        # numero do item (se aplicavel)
      "protheus_value": "1500.00",
      "xml_value":      "1500.00",
      "status":         "ok" | "divergent" | "skipped",
      "severity":       "ok" | "critical" | "warn" | "info",
      "note":           "...",
    }

O caller (auditor.py) filtra por `status='divergent'` para persistir em
FiscalAnomaly; o endpoint `/api/fiscal/document-audit` devolve a lista inteira.

Colunas oficiais Fertimaxi (Sprint 13):
- SDS: DS_DOC, DS_SERIE, DS_FORNEC, DS_LOJA, DS_CNPJ, DS_EMISSA, DS_CHAVENF,
       DS_VALMERC, DS_TOTAL, DS_BASEICM, DS_VALICM, DS_FRETE, DS_SEGURO,
       DS_DESCONT, DS_DESPESA
- SF1: F1_DOC, F1_SERIE, F1_FORNECE, F1_LOJA, F1_EMISSAO, F1_CHVNFE,
       F1_VALMERC, F1_VALBRUT, F1_BASEICM, F1_VALICM, F1_FRETE, F1_SEGURO,
       F1_DESCONT, F1_DESPESA
- SDT (apos aliasing em internal_audit): DT_DOC, DT_SERIE, DT_FORNEC, DT_LOJA,
       DT_ITEM, DT_COD, DT_DESC, DT_QUANT, DT_VUNIT, DT_TOTAL, DT_BASEICM,
       DT_VALICM, DT_PICM, DT_CFOP, DT_CLASFIS
- SD1: D1_DOC, D1_SERIE, D1_FORNECE, D1_LOJA, D1_ITEM, D1_COD, D1_QUANT,
       D1_VUNIT, D1_TOTAL, D1_PICM, D1_VALICM, D1_BASEICM, D1_CLASFIS,
       D1_DESC, D1_CF, D1_TEC
- SA2 (carregada por F1_FORNECE+F1_LOJA): A2_CGC, A2_NOME
"""
from __future__ import annotations

import logging
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any, Optional

from . import comparators

logger = logging.getLogger(__name__)

_DIGITS_RE = re.compile(r"\D+")


class FiscalRuleEngine:
    """Motor de auditoria completa.

    Uso:
        engine = FiscalRuleEngine(doc)
        report = engine.run()  # list[dict] — TODOS os campos auditados
    """

    SEV_CRITICAL = "critical"
    SEV_WARN = "warn"
    SEV_INFO = "info"
    SEV_OK = "ok"

    ST_OK = "ok"
    ST_DIV = "divergent"
    ST_SKIP = "skipped"

    DESC_SIMILARITY_THRESHOLD = 0.75   # 75% — cliente confirmou

    def __init__(self, doc: dict):
        self.doc = doc or {}
        self.sds: dict = self.doc.get("sds") or {}
        self.sf1: Optional[dict] = self.doc.get("sf1")
        self.sa2: Optional[dict] = self.doc.get("sa2")
        self.sdt_items: list[dict] = self.doc.get("sdt_items") or []
        self.sd1_items: list[dict] = self.doc.get("sd1_items") or []
        self.report: list[dict] = []

    # ---- Tolerancias --------------------------------------------------------

    @staticmethod
    def tol_valor() -> Decimal:
        return comparators.tol_valor()

    @staticmethod
    def tol_icms() -> Decimal:
        return comparators.tol_icms()

    @staticmethod
    def tol_qtd() -> Decimal:
        return comparators.tol_qtd()

    # ---- Normalizacao -------------------------------------------------------

    @staticmethod
    def _norm_decimal(v, places: int = 2) -> Optional[Decimal]:
        if v is None or v == "":
            return None
        try:
            if isinstance(v, str):
                v = v.strip().replace(",", ".")
            return Decimal(str(v)).quantize(Decimal(10) ** -places)
        except (InvalidOperation, ValueError, TypeError):
            return None

    @staticmethod
    def _norm_digits(v) -> str:
        return _DIGITS_RE.sub("", str(v or ""))

    @staticmethod
    def _norm_item_n(v) -> str:
        s = str(v or "").strip().lstrip("0")
        return s or str(v or "")

    @staticmethod
    def _fmt_date(yyyymmdd) -> str:
        s = str(yyyymmdd or "").strip()
        if len(s) == 8 and s.isdigit():
            return f"{s[6:8]}/{s[4:6]}/{s[0:4]}"
        return s

    @staticmethod
    def _g(d: Optional[dict], key: str) -> str:
        if not d:
            return ""
        v = d.get(key)
        if v is None:
            return ""
        return str(v).strip()

    @staticmethod
    def _normalize_text(s: str) -> str:
        """Tira acento + upper + colapsa whitespace (para similaridade)."""
        s = unicodedata.normalize("NFD", str(s or ""))
        s = "".join(c for c in s if unicodedata.category(c) != "Mn")
        return re.sub(r"\s+", " ", s).strip().upper()

    @classmethod
    def _text_similarity(cls, a: str, b: str) -> float:
        na, nb = cls._normalize_text(a), cls._normalize_text(b)
        if not na and not nb:
            return 1.0
        if not na or not nb:
            return 0.0
        if na == nb:
            return 1.0
        return SequenceMatcher(None, na, nb).ratio()

    # ---- Registro de campo no relatorio -------------------------------------

    def _emit(
        self,
        field: str,
        label: str,
        protheus_value: Any,
        xml_value: Any,
        status: str,
        severity: str,
        note: str = "",
        *,
        category: str = "header",
        item_n: Optional[str] = None,
    ) -> None:
        self.report.append({
            "field": field,
            "label": label,
            "category": category,
            "item_n": item_n,
            "protheus_value": "" if protheus_value is None else str(protheus_value),
            "xml_value": "" if xml_value is None else str(xml_value),
            "status": status,
            "severity": severity,
            "note": note,
        })

    def _emit_ok(self, field, label, p, x, *, category="header", item_n=None, note=""):
        self._emit(field, label, p, x, self.ST_OK, self.SEV_OK, note,
                   category=category, item_n=item_n)

    def _emit_div(self, field, label, p, x, severity, note, *,
                  category="header", item_n=None):
        self._emit(field, label, p, x, self.ST_DIV, severity, note,
                   category=category, item_n=item_n)

    def _emit_skip(self, field, label, p, x, note="dado ausente em um dos lados",
                   *, category="header", item_n=None):
        self._emit(field, label, p, x, self.ST_SKIP, self.SEV_INFO, note,
                   category=category, item_n=item_n)

    # ========================================================================
    #  R0 — Nota Ausente (SDS sem SF1)
    # ========================================================================

    def _check_nota_ausente(self) -> bool:
        if self.sf1 is not None:
            return False
        chave = self.doc.get("chave") or "(sem chave)"
        numero = self._g(self.sds, "DS_DOC")
        serie = self._g(self.sds, "DS_SERIE")
        self._emit(
            "nota_ausente", "Nota Ausente",
            protheus_value="(nao classificada)",
            xml_value=f"NF {numero}/{serie} chave {chave}",
            status=self.ST_DIV, severity=self.SEV_CRITICAL,
            note="XML importado mas a nota fiscal nao foi lancada no documento de entrada",
            category="header",
        )
        return True

    # ========================================================================
    #  Helpers de comparacao (cada um SEMPRE emite um item no relatorio)
    # ========================================================================

    def _check_string(self, field: str, label: str, p_raw: str, x_raw: str,
                      severity_if_div: str, note_div: str,
                      *, upper: bool = True, strip_zero: bool = False) -> None:
        p = (p_raw or "").strip()
        x = (x_raw or "").strip()
        if upper:
            p, x = p.upper(), x.upper()
        if strip_zero:
            p, x = p.lstrip("0"), x.lstrip("0")
        if not p and not x:
            self._emit_skip(field, label, p_raw, x_raw, "ambos vazios")
            return
        if not p or not x:
            self._emit_skip(field, label, p_raw, x_raw)
            return
        if p == x:
            self._emit_ok(field, label, p, x)
        else:
            self._emit_div(field, label, p, x, severity_if_div, note_div)

    def _check_decimal(self, field: str, label: str, p_raw, x_raw, tol: Decimal,
                       severity_if_div: str, places: int = 2) -> None:
        p = self._norm_decimal(p_raw, places=places)
        x = self._norm_decimal(x_raw, places=places)
        if p is None and x is None:
            self._emit_skip(field, label, p_raw, x_raw, "ambos sem valor numerico")
            return
        if p is None or x is None:
            self._emit_skip(field, label, p_raw, x_raw)
            return
        diff = abs(p - x)
        if diff <= tol:
            self._emit_ok(field, label, p, x,
                          note=f"diferenca {diff} dentro da tolerancia ({tol})")
        else:
            self._emit_div(
                field, label, p, x, severity_if_div,
                f"diverge em {diff} (tolerancia {tol})",
            )

    # ========================================================================
    #  Cabecalho (SDS x SF1) — 10 cruzamentos (ICMS base/valor saiu p/ nivel de item)
    # ========================================================================

    def _check_header(self) -> None:
        # 1) Numero
        self._check_string(
            "numero_nota", "Numero Doc",
            self._g(self.sf1, "F1_DOC"), self._g(self.sds, "DS_DOC"),
            self.SEV_CRITICAL, "Numero da NF diverge entre o documento lancado e o XML",
            strip_zero=True, upper=False,
        )
        # 2) Serie — XML pode trazer a serie em DS_SERIE OU DS_SDOC (ex: CTe).
        serie_xml = self._g(self.sds, "DS_SERIE") or self._g(self.sds, "DS_SDOC")
        self._check_string(
            "serie", "Serie",
            self._g(self.sf1, "F1_SERIE"), serie_xml,
            self.SEV_WARN, "Serie diverge entre o documento lancado e o XML",
        )
        # 3) Emissao
        p_em = self._g(self.sf1, "F1_EMISSAO")
        x_em = self._g(self.sds, "DS_EMISSA")
        if not p_em and not x_em:
            self._emit_skip("data_emissao", "Emissao", p_em, x_em)
        elif (len(p_em) == 8 and p_em.isdigit() and
              len(x_em) == 8 and x_em.isdigit()):
            if p_em == x_em:
                self._emit_ok("data_emissao", "Emissao",
                              self._fmt_date(p_em), self._fmt_date(x_em))
            else:
                self._emit_div(
                    "data_emissao", "Emissao",
                    self._fmt_date(p_em), self._fmt_date(x_em),
                    self.SEV_WARN,
                    "Data de emissao difere entre o documento lancado e o XML",
                )
        else:
            self._emit_skip("data_emissao", "Emissao", p_em, x_em,
                            "formato YYYYMMDD invalido em um dos lados")

        # 3b) Data de Digitacao/Classificacao (F1_DTDIGIT x DS_DATAIMP)
        p_dg = self._g(self.sf1, "F1_DTDIGIT")
        x_dg = self._g(self.sds, "DS_DATAIMP")
        if not p_dg and not x_dg:
            self._emit_skip("data_digitacao", "Digitacao/Importacao", p_dg, x_dg)
        elif (len(p_dg) == 8 and p_dg.isdigit() and
              len(x_dg) == 8 and x_dg.isdigit()):
            if p_dg == x_dg:
                self._emit_ok("data_digitacao", "Digitacao/Importacao",
                              self._fmt_date(p_dg), self._fmt_date(x_dg))
            else:
                self._emit_div(
                    "data_digitacao", "Digitacao/Importacao",
                    self._fmt_date(p_dg), self._fmt_date(x_dg),
                    self.SEV_WARN,
                    "Data de digitacao difere entre o documento lancado e a importacao do XML",
                )
        else:
            self._emit_skip("data_digitacao", "Digitacao/Importacao", p_dg, x_dg,
                            "formato YYYYMMDD invalido em um dos lados")

        # 3c) Especie do documento (F1_ESPECIE x DS_ESPECI)
        self._check_string(
            "especie", "Especie",
            self._g(self.sf1, "F1_ESPECIE"), self._g(self.sds, "DS_ESPECI"),
            self.SEV_WARN,
            "Especie diverge entre o documento lancado e o XML",
        )

        # 4) CNPJ — via SA2 (resolve F1_FORNECE+F1_LOJA -> A2_CGC)
        cnpj_xml = self._norm_digits(self._g(self.sds, "DS_CNPJ"))
        cnpj_pt = self._norm_digits(self._g(self.sa2, "A2_CGC")) if self.sa2 else ""
        forn_loja = f"{self._g(self.sf1,'F1_FORNECE')}/{self._g(self.sf1,'F1_LOJA')}"
        if not cnpj_xml and not cnpj_pt:
            self._emit_skip("cnpj_fornecedor", "Fornecedor (CNPJ)",
                            f"{forn_loja} -> (sem CNPJ no documento)", cnpj_xml)
        elif not cnpj_pt:
            self._emit_skip(
                "cnpj_fornecedor", "Fornecedor (CNPJ)",
                f"{forn_loja} -> (sem CNPJ no documento)", cnpj_xml,
                note="cadastro do fornecedor nao encontrado (sem CNPJ no documento lancado)",
            )
        elif not cnpj_xml:
            self._emit_skip(
                "cnpj_fornecedor", "Fornecedor (CNPJ)",
                f"{forn_loja} -> {cnpj_pt}", "(CNPJ vazio no XML)",
            )
        elif cnpj_pt == cnpj_xml:
            self._emit_ok(
                "cnpj_fornecedor", "Fornecedor (CNPJ)",
                f"{forn_loja} -> {cnpj_pt}", cnpj_xml,
            )
        else:
            self._emit_div(
                "cnpj_fornecedor", "Fornecedor (CNPJ)",
                f"{forn_loja} -> {cnpj_pt}", cnpj_xml,
                self.SEV_CRITICAL,
                "CNPJ do fornecedor diverge entre o documento lancado e o XML",
            )

        # 5) Vlr Mercadoria
        self._check_decimal(
            "valor_mercadoria", "Vlr Mercadoria",
            self._g(self.sf1, "F1_VALMERC"), self._g(self.sds, "DS_VALMERC"),
            self.tol_valor(), self.SEV_WARN,
        )
        # 6) Vlr Total
        self._check_decimal(
            "valor_total", "Vlr Total",
            self._g(self.sf1, "F1_VALBRUT"), self._g(self.sds, "DS_TOTAL"),
            self.tol_valor(), self.SEV_CRITICAL,
        )
        # (Base ICMS e Valor ICMS removidos do CABECALHO a pedido do cliente —
        #  o ICMS continua sendo auditado por ITEM, em _check_item_pair.)
        # 7) Frete
        self._check_decimal(
            "frete", "Frete",
            self._g(self.sf1, "F1_FRETE"), self._g(self.sds, "DS_FRETE"),
            self.tol_valor(), self.SEV_WARN,
        )
        # 8) Seguro
        self._check_decimal(
            "seguro", "Seguro",
            self._g(self.sf1, "F1_SEGURO"), self._g(self.sds, "DS_SEGURO"),
            self.tol_valor(), self.SEV_WARN,
        )
        # 9) Desconto
        self._check_decimal(
            "desconto", "Desconto",
            self._g(self.sf1, "F1_DESCONT"), self._g(self.sds, "DS_DESCONT"),
            self.tol_valor(), self.SEV_WARN,
        )
        # 10) Despesas
        self._check_decimal(
            "despesas", "Despesas",
            self._g(self.sf1, "F1_DESPESA"), self._g(self.sds, "DS_DESPESA"),
            self.tol_valor(), self.SEV_WARN,
        )

    # ========================================================================
    #  Itens (SDT x SD1) — 9 cruzamentos por item
    # ========================================================================

    def _index_sdt(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for r in self.sdt_items:
            n = self._norm_item_n(r.get("DT_ITEM"))
            if n and n not in out:
                out[n] = r
        return out

    def _check_items(self) -> None:
        sdt_by_n = self._index_sdt()
        seen_n: set[str] = set()
        for d1 in self.sd1_items:
            n = self._norm_item_n(d1.get("D1_ITEM"))
            seen_n.add(n)
            dt = sdt_by_n.get(n)
            if dt is None:
                self._emit_div(
                    f"item_{n}_ausente_xml", f"Item {n} (ausente no XML)",
                    self._g(d1, "D1_COD"), "(ausente em SDT)",
                    self.SEV_WARN,
                    "Item lancado no documento mas ausente no XML",
                    category="item", item_n=n,
                )
                continue
            self._check_item_pair(n, d1, dt)

        for n, dt in sdt_by_n.items():
            if n in seen_n:
                continue
            self._emit_div(
                f"item_{n}_ausente_sd1", f"Item {n} (ausente em SD1)",
                "(ausente em SD1)",
                self._g(dt, "DT_COD") or self._g(dt, "DT_DESC"),
                self.SEV_WARN,
                "Item presente no XML mas nao lancado no documento",
                category="item", item_n=n,
            )

    def _check_item_pair(self, n: str, d1: dict, dt: dict) -> None:
        cat = "item"
        # 1) Vlr Unitario
        self._check_decimal(
            f"item_{n}_valor_unit", f"Item {n} — Vlr Unitario",
            d1.get("D1_VUNIT"), dt.get("DT_VUNIT"),
            self.tol_valor(), self.SEV_WARN, places=4,
        )
        self._patch_last_meta(category=cat, item_n=n)
        # 2) Quantidade
        self._check_decimal(
            f"item_{n}_quantidade", f"Item {n} — Quantidade",
            d1.get("D1_QUANT"), dt.get("DT_QUANT"),
            self.tol_qtd(), self.SEV_WARN, places=4,
        )
        self._patch_last_meta(category=cat, item_n=n)
        # 3) Vlr Total
        self._check_decimal(
            f"item_{n}_valor_total", f"Item {n} — Vlr Total",
            d1.get("D1_TOTAL"), dt.get("DT_TOTAL"),
            self.tol_valor(), self.SEV_WARN,
        )
        self._patch_last_meta(category=cat, item_n=n)
        # 4) Base ICMS
        self._check_decimal(
            f"item_{n}_base_icms", f"Item {n} — Base ICMS",
            d1.get("D1_BASEICM"), dt.get("DT_BASEICM"),
            self.tol_icms(), self.SEV_CRITICAL,
        )
        self._patch_last_meta(category=cat, item_n=n)
        # 5) Vlr ICMS
        self._check_decimal(
            f"item_{n}_valor_icms", f"Item {n} — Valor ICMS",
            d1.get("D1_VALICM"), dt.get("DT_VALICM"),
            self.tol_icms(), self.SEV_CRITICAL,
        )
        self._patch_last_meta(category=cat, item_n=n)
        # 6) Aliquota ICMS
        self._check_decimal(
            f"item_{n}_aliquota_icms", f"Item {n} — Aliquota ICMS",
            d1.get("D1_PICM"), dt.get("DT_PICM"),
            Decimal("0.01"), self.SEV_CRITICAL, places=4,
        )
        self._patch_last_meta(category=cat, item_n=n)
        # 7) CFOP
        cfop_pt = self._norm_digits(d1.get("D1_CF"))
        cfop_xml = self._norm_digits(dt.get("DT_CFOP"))
        if not cfop_pt and not cfop_xml:
            self._emit_skip(f"item_{n}_cfop", f"Item {n} — CFOP",
                            cfop_pt, cfop_xml, category=cat, item_n=n)
        elif not cfop_pt or not cfop_xml:
            self._emit_skip(f"item_{n}_cfop", f"Item {n} — CFOP",
                            cfop_pt, cfop_xml, category=cat, item_n=n)
        elif cfop_pt == cfop_xml:
            self._emit_ok(f"item_{n}_cfop", f"Item {n} — CFOP",
                          cfop_pt, cfop_xml, category=cat, item_n=n)
        else:
            self._emit_div(f"item_{n}_cfop", f"Item {n} — CFOP",
                           cfop_pt, cfop_xml, self.SEV_CRITICAL,
                           "CFOP diverge entre o documento lancado e o XML",
                           category=cat, item_n=n)
        # 8) CST — SD1 (D1_CLASFIS) x XML (DT_CLASFIS). Considera APENAS o SD1 no
        #    lado ERP (SFT desconsiderado a pedido do cliente).
        cst_pt = self._norm_digits(d1.get("D1_CLASFIS"))
        cst_xml = self._norm_digits(dt.get("DT_CLASFIS"))
        if not cst_pt or not cst_xml:
            self._emit_skip(f"item_{n}_cst", f"Item {n} — CST",
                            cst_pt, cst_xml, category=cat, item_n=n)
        elif cst_pt == cst_xml:
            self._emit_ok(f"item_{n}_cst", f"Item {n} — CST",
                          cst_pt, cst_xml, category=cat, item_n=n)
        else:
            self._emit_div(f"item_{n}_cst", f"Item {n} — CST",
                           cst_pt, cst_xml, self.SEV_CRITICAL,
                           "CST diverge entre o documento lancado e o XML",
                           category=cat, item_n=n)
        # 9) Descricao do produto: D1_FSDPROD (ERP) x DT_DESCFOR (XML, alias DT_DESC).
        #    Fallback p/ D1_DESC se o campo do cliente nao existir. Similaridade > 75%.
        desc_pt = self._g(d1, "D1_FSDPROD") or self._g(d1, "D1_DESC")
        desc_xml = self._g(dt, "DT_DESC")
        if not desc_pt and not desc_xml:
            self._emit_skip(f"item_{n}_descricao", f"Item {n} — Descricao",
                            desc_pt, desc_xml, category=cat, item_n=n)
        elif not desc_pt or not desc_xml:
            self._emit_skip(f"item_{n}_descricao", f"Item {n} — Descricao",
                            desc_pt, desc_xml, category=cat, item_n=n)
        else:
            sim = self._text_similarity(desc_pt, desc_xml)
            if sim >= self.DESC_SIMILARITY_THRESHOLD:
                self._emit_ok(f"item_{n}_descricao", f"Item {n} — Descricao",
                              desc_pt[:80], desc_xml[:80],
                              note=f"similaridade {sim:.0%}",
                              category=cat, item_n=n)
            else:
                self._emit_div(
                    f"item_{n}_descricao", f"Item {n} — Descricao",
                    desc_pt[:80], desc_xml[:80], self.SEV_WARN,
                    f"Descricao diverge (similaridade {sim:.0%} < {int(self.DESC_SIMILARITY_THRESHOLD*100)}%)",
                    category=cat, item_n=n,
                )

    def _patch_last_meta(self, *, category: str, item_n: str) -> None:
        """Sobrescreve category/item_n no ultimo registro (helpers genericos
        sempre emitem com category='header'; metodos de item ajustam aqui)."""
        if self.report:
            self.report[-1]["category"] = category
            self.report[-1]["item_n"] = item_n

    # ========================================================================
    #  Entry point
    # ========================================================================

    def run(self) -> list[dict]:
        """Devolve a lista COMPLETA de campos auditados (ok + divergent + skipped)."""
        if self._check_nota_ausente():
            # Sem SF1/SD1 nao ha o que cruzar, mas listamos os itens do XML
            # internalizado (SDT) para a analista visualizar os dados.
            self._emit_xml_items_only()
            return self.report
        self._check_header()
        self._check_items()
        return self.report

    def _emit_xml_items_only(self) -> None:
        """Nota Ausente: expoe os itens do XML (SDT) como `skipped` — ERP vazio
        (sem nota classificada), valores do XML preenchidos, para analise."""
        XML_FIELDS = (
            ("descricao", "Descricao", "DT_DESC"),
            ("quantidade", "Quantidade", "DT_QUANT"),
            ("valor_unit", "Vlr Unitario", "DT_VUNIT"),
            ("valor_total", "Vlr Total", "DT_TOTAL"),
            ("base_icms", "Base ICMS", "DT_BASEICM"),
            ("valor_icms", "Valor ICMS", "DT_VALICM"),
            ("aliquota_icms", "Aliquota ICMS", "DT_PICM"),
            ("cfop", "CFOP", "DT_CFOP"),
            ("cst", "CST", "DT_CLASFIS"),
        )
        for dt in self.sdt_items:
            n = self._norm_item_n(dt.get("DT_ITEM"))
            for key, label, col in XML_FIELDS:
                val = self._g(dt, col)
                if val == "":
                    continue
                self._emit(
                    f"item_{n}_{key}", f"Item {n} — {label}",
                    protheus_value="(sem nota classificada)", xml_value=val,
                    status=self.ST_SKIP, severity=self.SEV_INFO,
                    note="XML importado — nota ainda nao lancada no documento de entrada",
                    category="item", item_n=n,
                )

    def divergences(self) -> list[dict]:
        """Atalho — devolve so os items com status='divergent'."""
        if not self.report:
            self.run()
        return [r for r in self.report if r.get("status") == self.ST_DIV]

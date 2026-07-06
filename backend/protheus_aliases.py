"""Catálogo de aliases Protheus permitidos no construtor visual.

Cada entrada mapeia o alias de 3 caracteres (ex.: SE1) para a descrição
amigável e o nome do campo "filial" daquela tabela. O nome físico real
da tabela é montado dinamicamente como `ALIAS + FILIAL + sufixo` (sufixo
configurável em PROTHEUS_TABLE_SUFFIX, padrão '0').

Regras:
- O frontend SÓ pode escolher aliases listados aqui.
- O nome do campo de filial é derivado por convenção Protheus:
  primeira letra do alias + "_FILIAL" — ex.: SE1 -> E1_FILIAL.
- Para a coluna do `*_FILIAL`, a regra cobre 99% das tabelas. Casos
  excepcionais (ex.: SAU_FILIAL não existe) são tratados em
  `branch_field()` retornando None.
"""
from __future__ import annotations

from typing import Optional

# (alias, label) — manter ordenado por alias para o select.
ALIASES: list[tuple[str, str]] = [
    ("ADA", "Contrato de Parceria"),
    ("ADB", "Itens do Contrato de Parceria"),
    ("CT2", "Lançamentos Contábeis"),
    ("CT5", "Lançamento Padrão"),
    ("DA4", "Motoristas"),
    ("FK5", "Movimentos Bancários"),
    ("FK6", "Valores Acessórios"),
    ("FKX", "Naturezas de Rendimento"),
    ("SA1", "Clientes"),
    ("SA2", "Fornecedores"),
    ("SA3", "Vendedores"),
    ("SA4", "Transportadoras"),
    ("SA5", "Amarração Produto X Fornecedor"),
    ("SA6", "Bancos"),
    ("SAB", "Comissões"),
    ("SAH", "Unidades de Medida"),
    ("SAI", "Solicitantes"),
    ("SAJ", "Grupos de Compras"),
    ("SAK", "Aprovadores"),
    ("SAL", "Grupos de Aprovação"),
    ("SAU", "Grupo de Filiais"),
    ("SB1", "Descrição Genérica do Produto"),
    ("SB5", "Dados Adicionais do Produto"),
    ("SB6", "Saldo Em Poder de Terceiros"),
    ("SB7", "Lançamento do Inventário"),
    ("SB8", "Saldos Por Lote"),
    ("SBM", "Grupo de Produto"),
    # Sprint 7 — descrições oficiais TOTVS (corrigido duplicado SC2/SC3 e SCS/SCV)
    ("SC2", "Ordens de Produção"),
    ("SC3", "Contrato de Parceria"),
    ("SC5", "Pedidos de Venda"),
    ("SC6", "Itens dos Pedidos de Venda"),
    ("SC7", "Pedidos de Compra / Aut. Entrega"),
    ("SC8", "Cotações"),
    ("SC9", "Pedidos Liberados"),
    ("SCB", "Contratos a Receber"),
    ("SCH", "Rateio Pedido de Compra"),
    ("SCP", "Solicitações ao Armazém"),
    ("SCR", "Documentos com Alçada"),
    ("SCS", "Saldos dos Aprovadores"),
    ("SCV", "Itens dos Saldos dos Aprovadores"),
    ("SCY", "Histórico de Pedidos de Compra"),
    ("SD0", "Programação de Entrega"),
    ("SD1", "Itens das NF de Entrada"),
    ("SD2", "Itens de Venda da NF"),
    ("SD3", "Movimentações Internas"),
    ("SD4", "Requisições Empenhadas"),
    ("SD6", "Itens do Contrato"),
    ("SE1", "Contas a Receber"),
    ("SE2", "Contas a Pagar"),
    ("SE3", "Comissões de Vendas"),
    ("SE4", "Condições de Pagamento"),
    ("SE5", "Movimentação Bancária"),
    ("SF1", "Cabeçalho das NF de Entrada"),
    ("SF2", "Cabeçalho das NF de Saída"),
    ("SF3", "Livros Fiscais"),
    ("SF4", "Tipos de Entrada e Saída"),
    ("SF5", "Tipos de Movimentação"),
    ("SZ3", "Tipo de Serviço"),
    ("Z00", "Ordem de Carregamento"),
    ("Z2C", "Cabeçalho de Grupo Por Produto"),
    ("Z2D", "Itens de Grupo Por Produto"),
    ("Z59", "Pré-venda"),
    ("Z62", "Aprovadores de Venda"),
    ("Z72", "Natureza do Produto"),
    ("ZA4", "Ticket de Pesagem"),
    ("ZA6", "Itens de Composição"),
    ("ZA7", "Cabeç. Regras Operações Saída"),
    ("ZA8", "Itens Regras Operações Saída"),
    ("ZAI", "Pré Ordem de Carregamento"),
    ("ZAM", "Clientes Peso OC"),
    ("ZAQ", "Origem X Armazém"),
    ("ZC0", "Armazém"),
    ("ZC1", "Operações de Entrada"),
    ("ZC2", "Regras de Operações de Entrada"),
    ("ZC3", "Itens das Regras de Operação"),
    ("ZC4", "Ordem de Descarregamento"),
    ("ZC5", "Autorização Entrega"),
    ("ZC7", "Regras de Oper. Transferência"),
    ("ZC8", "Ordem de Transferência"),
    ("ZF4", "Itens do XML de Entrada"),
]

ALIAS_SET = {a for a, _ in ALIASES}


def is_known_alias(alias: str) -> bool:
    return (alias or "").upper() in ALIAS_SET


def branch_field(alias: str) -> Optional[str]:
    """Nome do campo *_FILIAL daquela tabela, segundo convenção Protheus.

    Ex.: SE1 -> E1_FILIAL ; ZC0 -> ZC0_FILIAL (Z** são exceção: 3 letras).
    Retorna None se não há campo de filial conhecido.
    """
    a = (alias or "").upper()
    if len(a) != 3:
        return None
    # Z** quase sempre mantém os 3 caracteres no prefixo do campo.
    if a.startswith("Z"):
        return f"{a}_FILIAL"
    # Demais (S**, A**, C**, D**, F**, K**) usam os 2 últimos chars.
    return f"{a[1:]}_FILIAL"

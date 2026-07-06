"""Catalogo dos 8 perfis canonicos + matriz default alias -> perfis.

Esses dados sao inseridos UMA VEZ no `lifespan` (`seed_profiles_and_matrix`).
Apos o seed, admins editam tudo pela UI sem precisar tocar este arquivo.

Filosofia:
- O admin TEM PODER de editar tudo: nomes dos perfis, descricoes, e matriz.
- O seed so popula a base na primeira subida — depois nunca sobrescreve.
- Aliases nao mapeados ficam acessiveis APENAS via whitelist direta
  (`UserTablePermission`) — a matriz e' um conveniente "atalho", nao a unica
  forma de dar acesso.
"""
from __future__ import annotations

# === Os 8 perfis canonicos (Fertimaxi) ========================================
# code -> (label_amigavel, descricao_curta)
SEED_PROFILES: dict[str, tuple[str, str]] = {
    "LOGISTICA":      ("Logística",      "Transporte, expedição, ordens de carregamento/descarregamento"),
    "CONTABIL":       ("Contábil",       "Lançamentos contábeis, livros fiscais, fechamento"),
    "CONTROLADORIA":  ("Controladoria",  "Visão consolidada para análise e compliance"),
    "FINANCEIRO":     ("Financeiro",     "Contas a pagar/receber, bancos, comissões"),
    "PCP":            ("PCP",            "Planejamento e controle da produção, compras"),
    "ESTOQUE":        ("Estoque",        "Almoxarifado, inventário, saldos por lote"),
    "ADMINISTRATIVO": ("Administrativo", "Aprovações, alçadas, parâmetros gerais"),
    "COMERCIAL":      ("Comercial",      "Vendas, clientes, pedidos, comissões"),
}


# === Matriz default alias -> [perfis] =========================================
# Cada alias pode aparecer em 1+ perfis (overlap intencional: SF1 e' tanto
# contabil quanto controladoria, por exemplo).
# Aliases nao listados aqui ficam sem perfil ate o admin associa-los.
SEED_MATRIX: dict[str, list[str]] = {
    # --- COMERCIAL ---
    "SA1": ["COMERCIAL"],                          # Clientes
    "SA3": ["COMERCIAL"],                          # Vendedores
    "SAB": ["COMERCIAL"],                          # Comissoes
    "SC5": ["COMERCIAL"],                          # Pedidos de Venda
    "SC6": ["COMERCIAL"],                          # Itens dos Pedidos de Venda
    "SC9": ["COMERCIAL", "PCP"],                   # Pedidos Liberados
    "SD2": ["COMERCIAL", "CONTROLADORIA"],         # Itens de Venda da NF
    "SE3": ["COMERCIAL", "FINANCEIRO"],            # Comissoes de Vendas
    "SF2": ["COMERCIAL", "CONTABIL"],              # Cabecalho NFe Saida
    "Z59": ["COMERCIAL"],                          # Pre-venda
    "Z62": ["COMERCIAL", "ADMINISTRATIVO"],        # Aprovadores de Venda
    "Z2C": ["COMERCIAL", "ESTOQUE"],               # Cabec. Grupo Por Produto
    "Z2D": ["COMERCIAL", "ESTOQUE"],               # Itens Grupo Por Produto
    "ZAM": ["COMERCIAL", "LOGISTICA"],             # Clientes Peso OC
    "ADA": ["COMERCIAL"],                          # Contrato de Parceria
    "ADB": ["COMERCIAL"],                          # Itens do Contrato de Parceria

    # --- FINANCEIRO ---
    "SA2": ["FINANCEIRO"],                         # Fornecedores
    "SA6": ["FINANCEIRO"],                         # Bancos
    "SE1": ["FINANCEIRO"],                         # Contas a Receber
    "SE2": ["FINANCEIRO"],                         # Contas a Pagar
    "SE4": ["FINANCEIRO", "ADMINISTRATIVO"],       # Condicoes de Pagamento
    "SE5": ["FINANCEIRO"],                         # Movimentacao Bancaria
    "FK5": ["FINANCEIRO"],                         # Movimentos Bancarios
    "FK6": ["FINANCEIRO"],                         # Valores Acessorios
    "FKX": ["FINANCEIRO"],                         # Naturezas de Rendimento

    # --- CONTABIL / CONTROLADORIA ---
    "CT2": ["CONTABIL"],                           # Lancamentos Contabeis
    "CT5": ["CONTABIL"],                           # Lancamento Padrao
    "SF1": ["CONTABIL", "CONTROLADORIA"],          # Cabecalho NFe Entrada
    "SD1": ["CONTABIL", "CONTROLADORIA"],          # Itens NF Entrada
    "SF3": ["CONTABIL"],                           # Livros Fiscais
    "SF4": ["CONTABIL", "ADMINISTRATIVO"],         # Tipos de Entrada e Saida
    "SCH": ["PCP", "CONTROLADORIA"],               # Rateio Pedido de Compra
    "ZF4": ["CONTABIL", "CONTROLADORIA"],          # Itens XML de Entrada

    # --- LOGISTICA ---
    "SA4": ["LOGISTICA"],                          # Transportadoras
    "DA4": ["LOGISTICA"],                          # Motoristas
    "SD0": ["LOGISTICA", "PCP"],                   # Programacao de Entrega
    "Z00": ["LOGISTICA"],                          # Ordem de Carregamento
    "ZA4": ["LOGISTICA"],                          # Ticket de Pesagem
    "ZA6": ["LOGISTICA"],                          # Itens de Composicao
    "ZA7": ["LOGISTICA"],                          # Cab. Regras Operacoes Saida
    "ZA8": ["LOGISTICA"],                          # Itens Regras Operacoes Saida
    "ZAI": ["LOGISTICA"],                          # Pre Ordem de Carregamento
    "ZAQ": ["LOGISTICA", "ESTOQUE"],               # Origem X Armazem
    "ZC1": ["LOGISTICA", "CONTABIL"],              # Operacoes de Entrada
    "ZC2": ["LOGISTICA"],                          # Regras Operacoes Entrada
    "ZC3": ["LOGISTICA"],                          # Itens Regras Operacao
    "ZC4": ["LOGISTICA"],                          # Ordem de Descarregamento
    "ZC5": ["LOGISTICA"],                          # Autorizacao Entrega
    "ZC7": ["LOGISTICA"],                          # Regras Transferencia
    "ZC8": ["LOGISTICA"],                          # Ordem de Transferencia

    # --- ESTOQUE ---
    "SA5": ["ESTOQUE"],                            # Amarracao Produto X Fornecedor
    "SAH": ["ESTOQUE", "ADMINISTRATIVO"],          # Unidades de Medida
    "SB1": ["ESTOQUE"],                            # Descricao Generica do Produto
    "SB5": ["ESTOQUE"],                            # Dados Adicionais do Produto
    "SB6": ["ESTOQUE"],                            # Saldo Em Poder de Terceiros
    "SB7": ["ESTOQUE"],                            # Lancamento Inventario
    "SB8": ["ESTOQUE"],                            # Saldos Por Lote
    "SBM": ["ESTOQUE"],                            # Grupo de Produto
    "SD3": ["ESTOQUE"],                            # Movimentacoes Internas
    "SD4": ["ESTOQUE", "PCP"],                     # Requisicoes Empenhadas
    "SF5": ["ESTOQUE", "ADMINISTRATIVO"],          # Tipos de Movimentacao
    "Z72": ["ESTOQUE"],                            # Natureza do Produto
    "ZC0": ["ESTOQUE"],                            # Armazem

    # --- PCP ---
    "SC2": ["PCP"],                                # Ordens de Producao
    "SC3": ["PCP"],                                # Ordens de Producao
    "SC7": ["PCP"],                                # Ped.compra / Aut.entrega
    "SC8": ["PCP"],                                # Cotacoes
    "SCB": ["PCP"],                                # Contrato
    "SCP": ["PCP", "ESTOQUE"],                     # Solicitacoes Ao Armazem
    "SCY": ["PCP"],                                # Historico Pedidos de Compras
    "SD6": ["PCP"],                                # Itens do Contrato
    "SAJ": ["PCP", "ADMINISTRATIVO"],              # Grupos de Compras

    # --- ADMINISTRATIVO ---
    "SAI": ["ADMINISTRATIVO"],                     # Solicitantes
    "SAK": ["ADMINISTRATIVO"],                     # Aprovadores
    "SAL": ["ADMINISTRATIVO"],                     # Grupos de Aprovacao
    "SAU": ["ADMINISTRATIVO"],                     # Grupo de Filiais
    "SCR": ["ADMINISTRATIVO"],                     # Documentos Com Alcada
    "SCS": ["ADMINISTRATIVO"],                     # Saldos dos Aprovadores
    "SCV": ["ADMINISTRATIVO"],                     # Saldos dos Aprovadores
    "SZ3": ["ADMINISTRATIVO"],                     # Tipo de Servico
}


def all_aliases_in_matrix() -> set[str]:
    """Util para diagnostico — quais aliases estao na matriz default."""
    return set(SEED_MATRIX.keys())


# === Seed idempotente ========================================================

def run_seed() -> dict:
    """Insere os 8 perfis + matriz default UMA VEZ.

    Idempotencia:
    - Cada perfil so e' criado se nao existir (pelo `code`).
    - Matriz so e' inserida se a tabela `table_profiles` estiver VAZIA — se o
      admin ja editou a matriz, NAO sobrescrevemos.

    Retorna `{"profiles_created": int, "matrix_rows": int}` para log.
    """
    # Late-import para evitar ciclo com models.py
    from .database import SessionLocal
    from .models import Profile, TableProfile

    db = SessionLocal()
    try:
        existing_codes = {p.code for p in db.query(Profile).all()}
        profiles_created = 0
        for code, (label, desc) in SEED_PROFILES.items():
            if code in existing_codes:
                continue
            db.add(Profile(code=code, label=label, description=desc))
            profiles_created += 1
        db.commit()

        # Matriz default: so popula se a tabela esta vazia (primeira subida).
        matrix_rows = 0
        if db.query(TableProfile).first() is None:
            id_by_code = {p.code: p.id for p in db.query(Profile).all()}
            for alias, codes in SEED_MATRIX.items():
                for code in codes:
                    pid = id_by_code.get(code)
                    if pid is None:
                        continue
                    db.add(TableProfile(alias=alias.upper(), profile_id=pid))
                    matrix_rows += 1
            db.commit()

        return {"profiles_created": profiles_created, "matrix_rows": matrix_rows}
    finally:
        db.close()

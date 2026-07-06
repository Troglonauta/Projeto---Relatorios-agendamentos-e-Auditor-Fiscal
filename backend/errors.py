"""Catalogo de erros tipados — Fase 3 Sprint 4.

Cada erro tem:
- `code`     : `ERR-XXX-NNN` (camada + numero) — referencia o catalogo em
               `docs/ERROR_CATALOG.md`.
- `message`  : texto amigavel para o usuario final.
- `http_status` : codigo HTTP padrao.
- `detail`   : informacao tecnica adicional (logs/auditoria).

Uso:
    from backend.errors import AppError, ERR_DB_001
    raise ERR_DB_001(detail="DSN invalido para HOST=...")

Ou via factory dinamico:
    raise AppError("ERR-PROTHEUS-002", detail="...")

Erros NAO sao expostos em FastAPI por exception_handler global aqui — cada
router pode capturar `AppError` e converter para HTTPException com o code
no body, OU usar o handler global em `main.py` (chamando `register_handlers`).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


# ---- Catalogo ---------------------------------------------------------------

@dataclass(frozen=True)
class ErrorSpec:
    code: str
    message: str
    http_status: int = 500


# Camada DB (conexao SQL Server Protheus)
ERR_DB_001 = ErrorSpec("ERR-DB-001", "Falha ao conectar no banco Protheus",                       502)
ERR_DB_002 = ErrorSpec("ERR-DB-002", "Driver ODBC nao encontrado",                                500)
ERR_DB_003 = ErrorSpec("ERR-DB-003", "Login Protheus negado pelo servidor",                       502)
ERR_DB_004 = ErrorSpec("ERR-DB-004", "Timeout na consulta Protheus",                              504)
ERR_DB_005 = ErrorSpec("ERR-DB-005", "Pool de conexoes Protheus exaurido",                        503)

# Camada AUTH
ERR_AUTH_001 = ErrorSpec("ERR-AUTH-001", "Usuario ou senha invalidos",                            401)
ERR_AUTH_002 = ErrorSpec("ERR-AUTH-002", "Token JWT expirado",                                    401)
ERR_AUTH_003 = ErrorSpec("ERR-AUTH-003", "Sem permissao para esta tabela",                        403)
ERR_AUTH_004 = ErrorSpec("ERR-AUTH-004", "Sem permissao para esta acao",                          403)
ERR_AUTH_005 = ErrorSpec("ERR-AUTH-005", "Sessao encerrada por inatividade",                      401)

# Camada SMTP
ERR_SMTP_001 = ErrorSpec("ERR-SMTP-001", "Servidor SMTP inalcancavel",                            502)
ERR_SMTP_002 = ErrorSpec("ERR-SMTP-002", "Credenciais SMTP rejeitadas",                           502)
ERR_SMTP_003 = ErrorSpec("ERR-SMTP-003", "STARTTLS recusado pelo servidor",                       502)
ERR_SMTP_004 = ErrorSpec("ERR-SMTP-004", "Relay negado — verifique o remetente autorizado",       502)
ERR_SMTP_005 = ErrorSpec("ERR-SMTP-005", "Anexo excede o limite do servidor SMTP",                413)

# Camada JOB (fila Celery)
ERR_JOB_001 = ErrorSpec("ERR-JOB-001", "Fila indisponivel — broker offline",                      503)
ERR_JOB_002 = ErrorSpec("ERR-JOB-002", "Job perdido — worker reiniciado durante execucao",        500)
ERR_JOB_003 = ErrorSpec("ERR-JOB-003", "Job cancelado pelo usuario",                              409)
ERR_JOB_004 = ErrorSpec("ERR-JOB-004", "Dataset excede o limite do formato",                      413)
ERR_JOB_005 = ErrorSpec("ERR-JOB-005", "Erro critico no worker",                                  500)
ERR_JOB_006 = ErrorSpec("ERR-JOB-006", "Payload de job invalido",                                 422)

# Camada PROTHEUS (logica de tabelas)
ERR_PROTHEUS_001 = ErrorSpec("ERR-PROTHEUS-001", "Tabela Protheus nao existe",                    404)
ERR_PROTHEUS_002 = ErrorSpec("ERR-PROTHEUS-002", "Coluna invalida — caracteres nao permitidos",   400)
ERR_PROTHEUS_003 = ErrorSpec("ERR-PROTHEUS-003", "Filtro malformado",                             400)

# Camada FISCAL (Auditor Fiscal)
ERR_FISCAL_001 = ErrorSpec("ERR-FISCAL-001", "Fonte XML nao configurada",                         412)
ERR_FISCAL_002 = ErrorSpec("ERR-FISCAL-002", "XML nao encontrado na fonte (NFe nao distribuida)", 404)
ERR_FISCAL_003 = ErrorSpec("ERR-FISCAL-003", "Chave de acesso NFe invalida (44 digitos)",         400)
ERR_FISCAL_004 = ErrorSpec("ERR-FISCAL-004", "Certificado A1 invalido ou expirado",               500)
ERR_FISCAL_005 = ErrorSpec("ERR-FISCAL-005", "Falha generica no Auditor Fiscal",                  500)
ERR_FISCAL_006 = ErrorSpec("ERR-FISCAL-006", "NCM divergente entre XML e cadastro SB1 (compliance)", 422)

# Camada CFG (settings e master key)
ERR_CFG_001 = ErrorSpec("ERR-CFG-001", "MASTER_KEY invalida ou ausente",                          500)
ERR_CFG_002 = ErrorSpec("ERR-CFG-002", "Setting solicitado nao encontrado",                       404)
ERR_CFG_003 = ErrorSpec("ERR-CFG-003", "Hot-reload de configuracao falhou",                       500)

# Indice rapido por codigo
_BY_CODE: dict[str, ErrorSpec] = {
    s.code: s for s in (
        ERR_DB_001, ERR_DB_002, ERR_DB_003, ERR_DB_004, ERR_DB_005,
        ERR_AUTH_001, ERR_AUTH_002, ERR_AUTH_003, ERR_AUTH_004, ERR_AUTH_005,
        ERR_SMTP_001, ERR_SMTP_002, ERR_SMTP_003, ERR_SMTP_004, ERR_SMTP_005,
        ERR_JOB_001, ERR_JOB_002, ERR_JOB_003, ERR_JOB_004, ERR_JOB_005, ERR_JOB_006,
        ERR_PROTHEUS_001, ERR_PROTHEUS_002, ERR_PROTHEUS_003,
        ERR_FISCAL_001, ERR_FISCAL_002, ERR_FISCAL_003, ERR_FISCAL_004, ERR_FISCAL_005, ERR_FISCAL_006,
        ERR_CFG_001, ERR_CFG_002, ERR_CFG_003,
    )
}


# ---- Excecao principal ------------------------------------------------------

class AppError(Exception):
    """Excecao tipada com codigo do catalogo.

    Pode receber:
    - Um `ErrorSpec` direto:  raise AppError(ERR_DB_001, detail="...")
    - Um codigo string:       raise AppError("ERR-DB-001", detail="...")
    """
    def __init__(self, spec, detail: str | None = None):
        if isinstance(spec, str):
            resolved = _BY_CODE.get(spec)
            if resolved is None:
                raise ValueError(f"Codigo de erro desconhecido: {spec}")
            spec = resolved
        self.spec: ErrorSpec = spec
        self.detail: str | None = detail
        super().__init__(f"[{spec.code}] {spec.message}" + (f" — {detail}" if detail else ""))

    @property
    def code(self) -> str: return self.spec.code

    @property
    def message(self) -> str: return self.spec.message

    @property
    def http_status(self) -> int: return self.spec.http_status

    def to_dict(self) -> dict:
        return {
            "error_code": self.code,
            "message": self.message,
            "detail": self.detail or "",
        }


# Factories convenientes (cada ErrorSpec vira callable que cria AppError)
def _factory(spec: ErrorSpec):
    def _maker(detail: str | None = None) -> AppError:
        return AppError(spec, detail=detail)
    _maker.__name__ = spec.code.replace("-", "_")
    return _maker


# Conveniencias mais usadas — outros codigos via AppError("ERR-XYZ-NNN", detail=...)
db_unreachable        = _factory(ERR_DB_001)
no_odbc_driver        = _factory(ERR_DB_002)
db_auth_failed        = _factory(ERR_DB_003)
queue_unavailable     = _factory(ERR_JOB_001)
job_orphan            = _factory(ERR_JOB_002)
job_canceled          = _factory(ERR_JOB_003)
row_limit_exceeded    = _factory(ERR_JOB_004)
worker_critical       = _factory(ERR_JOB_005)
fiscal_no_source      = _factory(ERR_FISCAL_001)
fiscal_xml_not_found  = _factory(ERR_FISCAL_002)
fiscal_invalid_key    = _factory(ERR_FISCAL_003)
fiscal_a1_invalid     = _factory(ERR_FISCAL_004)
master_key_invalid    = _factory(ERR_CFG_001)
setting_not_found     = _factory(ERR_CFG_002)
reload_failed         = _factory(ERR_CFG_003)


# ---- Handler global FastAPI -------------------------------------------------

def register_handlers(app: FastAPI) -> None:
    """Registra exception_handler para `AppError` no FastAPI.

    Resposta JSON padrao:
        {
            "error_code": "ERR-DB-001",
            "message": "Falha ao conectar no banco Protheus",
            "detail": "..."
        }
    """
    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError):
        logger.warning("AppError em %s %s: %s", request.method, request.url.path, exc)
        return JSONResponse(
            status_code=exc.http_status,
            content=exc.to_dict(),
        )

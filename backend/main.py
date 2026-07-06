"""Ponto de entrada da aplicacao FastAPI.

Inicializa o app, monta os routers, cria as tabelas, arranca o scheduler.

Fase 3:
- `ensure_master_key()` no lifespan (Fernet) antes de qualquer settings_store.
- Migracao automatica `.env -> AppSetting` no primeiro boot.
- Endpoint publico `/api/branding/logo` com fallback Fertimaxi.
- Rotas protegidas por `require_setup_complete` quando `setup_complete=False`.
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .database import Base, engine
from .routers import (
    admin_routes, audit_routes, auth_routes, dashboard_routes, fiscal_routes,
    jobs_routes, profiles_routes, protheus_routes, saved_queries_routes,
    schedules_routes, settings_routes, setup_routes, users_routes,
)
from .security import settings_store
from .security.crypto import ensure_master_key
from . import scheduler
from . import errors as app_errors
from . import profiles_seed
from .version import BUILD_DATE, VERSION, version_info

settings = get_settings()
logger = logging.getLogger(__name__)


def _ensure_sqlite_columns() -> None:
    """Migracao minima p/ SQLite — adiciona colunas novas se faltarem.
    Idempotente (checa PRAGMA antes do ALTER). No-op em outros bancos."""
    if not settings.DATABASE_URL.startswith("sqlite"):
        return
    wanted = {
        "active_sessions": [("last_seen_at", "DATETIME")],
        # v2.21 — quem criou o usuario (auditoria visivel na tela de Usuarios).
        "users": [("created_by", "VARCHAR(60)")],
    }
    try:
        with engine.connect() as conn:
            for table, cols in wanted.items():
                existing = {row[1] for row in conn.exec_driver_sql(
                    f"PRAGMA table_info({table})"
                )}
                for col, decl in cols:
                    if col not in existing:
                        conn.exec_driver_sql(
                            f"ALTER TABLE {table} ADD COLUMN {col} {decl}"
                        )
                        logger.info("Migracao: coluna %s.%s adicionada", table, col)
            conn.commit()
    except Exception as exc:
        logger.warning("Migracao de colunas falhou (segue): %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "Iniciando %s v%s (build %s) — TZ=America/Sao_Paulo",
        settings.APP_NAME, VERSION, BUILD_DATE,
    )
    # 1) Inicializa criptografia (gera MASTER_KEY se ausente).
    ensure_master_key()
    # 2) Cria tabelas (inclui AppSetting, Job, FiscalAnomaly).
    Base.metadata.create_all(bind=engine)
    # 2b) Migracao leve: garante colunas novas em tabelas ja existentes
    #     (create_all NAO altera tabelas — adicionamos a mao, idempotente).
    _ensure_sqlite_columns()
    # 3) Migra .env -> AppSetting (idempotente).
    try:
        settings_store.migrate_env_to_settings()
    except Exception as exc:
        logger.warning("Migracao .env -> AppSetting falhou (segue mesmo assim): %s", exc)
    # 4) Seed dos 8 perfis canonicos + matriz default (idempotente).
    try:
        seed = profiles_seed.run_seed()
        if seed["profiles_created"] or seed["matrix_rows"]:
            logger.info(
                "Seed de perfis: %d criados, %d linhas na matriz.",
                seed["profiles_created"], seed["matrix_rows"],
            )
    except Exception as exc:
        logger.warning("Seed de perfis falhou (segue mesmo assim): %s", exc)

    # 5) Sprint 6 — pre-carrega dicionario SX3 (best-effort, nao bloqueia).
    #    Se o Protheus nao estiver acessivel ainda, sera lazy-load na 1a chamada.
    try:
        from . import dict_sx3
        res = dict_sx3.load_sx3()
        if res.get("loaded"):
            logger.info("SX3 pre-carregado: %d campos de '%s'",
                        res["count"], res.get("source"))
    except Exception as exc:
        logger.warning("Pre-load SX3 falhou (segue, sera lazy): %s", exc)

    # 6) Inicia scheduler (timezone Brasilia ja embutido em scheduler.py).
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(
    title=settings.APP_NAME,
    version="3.0.0",
    description="Plataforma comercial de extracao e auditoria de dados do TOTVS Protheus.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Handler global para AppError — responde JSON {error_code, message, detail}.
app_errors.register_handlers(app)


# ---- Guard de setup --------------------------------------------------------

def require_setup_complete():
    """Bloqueia rotas de uso normal enquanto o Wizard nao for finalizado."""
    if not settings_store.setup_complete():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Setup nao finalizado. Acesse /static/pages/setup.html",
        )


# ---- Routers sempre disponiveis (auth, settings publico, setup) ------------
app.include_router(auth_routes.router)
app.include_router(settings_routes.router)
app.include_router(setup_routes.router)

# ---- Routers que exigem setup completo -------------------------------------
SETUP_GUARDED = [Depends(require_setup_complete)]
app.include_router(users_routes.router,    dependencies=SETUP_GUARDED)
app.include_router(protheus_routes.router, dependencies=SETUP_GUARDED)
app.include_router(schedules_routes.router, dependencies=SETUP_GUARDED)
app.include_router(audit_routes.router,    dependencies=SETUP_GUARDED)
app.include_router(jobs_routes.router,     dependencies=SETUP_GUARDED)
app.include_router(fiscal_routes.router,   dependencies=SETUP_GUARDED)
app.include_router(dashboard_routes.router, dependencies=SETUP_GUARDED)
app.include_router(admin_routes.router,    dependencies=SETUP_GUARDED)
app.include_router(profiles_routes.router, dependencies=SETUP_GUARDED)
app.include_router(profiles_routes.user_profiles_router, dependencies=SETUP_GUARDED)
# Sprint 8 Part 1 — Consultas Salvas do Builder (per-user)
app.include_router(saved_queries_routes.router, dependencies=SETUP_GUARDED)


# ---- Branding (logo do cliente, com fallback Fertimaxi) --------------------

BRANDING_DIR = Path(__file__).resolve().parent.parent / "data" / "branding"
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
DEFAULT_LOGO = FRONTEND_DIR / "img" / "logo.png"


@app.get("/api/branding/logo", include_in_schema=False)
def branding_logo():
    custom = BRANDING_DIR / "logo.png"
    if custom.exists():
        return FileResponse(str(custom), media_type="image/png")
    if DEFAULT_LOGO.exists():
        return FileResponse(str(DEFAULT_LOGO), media_type="image/png")
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Sem logo configurada")


# ---- Frontend estatico -----------------------------------------------------

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def root():
        # Roteia para setup se ainda nao foi finalizado.
        if not settings_store.setup_complete():
            return RedirectResponse(url="/static/pages/setup.html")
        return RedirectResponse(url="/static/pages/login.html")

    @app.get("/health", include_in_schema=False)
    def health():
        return {
            "status": "ok",
            "app": settings.APP_NAME,
            "setup_complete": settings_store.setup_complete(),
            "timezone": "America/Sao_Paulo",
            **version_info(),
        }

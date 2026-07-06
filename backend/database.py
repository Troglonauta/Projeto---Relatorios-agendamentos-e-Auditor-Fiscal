"""Configuração da engine SQLAlchemy e da `SessionLocal`.

Suporta SQLite (dev) e SQL Server (prod) via DATABASE_URL no .env.
"""
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import get_settings

settings = get_settings()

# SQLite precisa do connect_args para multithread (FastAPI roda async + worker do scheduler).
connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}

# Garante que a pasta do SQLite exista antes de o SQLAlchemy abrir o arquivo.
if settings.DATABASE_URL.startswith("sqlite:///"):
    db_path = Path(settings.DATABASE_URL.replace("sqlite:///", "")).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
    future=True,
)


# Web (uvicorn) e worker (Celery) compartilham o mesmo app.db. Em modo journal
# default o SQLite serializa escritas e leitores bloqueiam escritores, gerando
# "database is locked" sob auditorias concorrentes. WAL + busy_timeout resolve.
if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_con, _record):  # noqa: ANN001
        cur = dbapi_con.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA cache_size=-16000")  # ~16 MB de cache de paginas
        cur.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
Base = declarative_base()


def get_db():
    """Dependência do FastAPI: abre/fecha sessão por request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

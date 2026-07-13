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
        # Recipe oficial SQLAlchemy p/ concorrencia: desliga o BEGIN implicito do
        # pysqlite (nos controlamos a transacao no evento "begin" abaixo). Sem
        # isso, as transacoes comecam como leitura e "sobem" para escrita,
        # criando um deadlock que o busy_timeout NAO respeita -> "database is
        # locked" mesmo com timeout alto (web x worker).
        dbapi_con.isolation_level = None
        cur = dbapi_con.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        # 30s: escritas concorrentes (web x worker) enfileiram em vez de falhar.
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA cache_size=-16000")  # ~16 MB de cache de paginas
        cur.close()

    @event.listens_for(engine, "begin")
    def _sqlite_begin_immediate(conn):  # noqa: ANN001
        # Adquire o lock de escrita ja no inicio da transacao -> o busy_timeout
        # passa a valer e as escritas enfileiram de forma justa.
        conn.exec_driver_sql("BEGIN IMMEDIATE")


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)
Base = declarative_base()


def get_db():
    """Dependência do FastAPI: abre/fecha sessão por request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

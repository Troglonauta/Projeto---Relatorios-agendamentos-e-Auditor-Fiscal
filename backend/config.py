"""Carregamento centralizado de variáveis de ambiente.

Tudo que é segredo (JWT, conexão Protheus, SMTP) sai daqui — nunca dos
módulos de negócio. Em produção basta substituir o `.env`.
"""
from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ===== Aplicação =====
    APP_NAME: str = "Protheus Reports — Fertimaxi"
    APP_ENV: str = "development"
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    # Fuso oficial — usado por scheduler, geração de relatório e timestamps.
    APP_TIMEZONE: str = "America/Sao_Paulo"

    # ===== Segurança =====
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 480
    # Janela máxima de inatividade antes do auto-logout (frontend).
    SESSION_IDLE_MINUTES: int = 20

    # ===== Banco LOCAL da aplicação =====
    DATABASE_URL: str = "sqlite:///./data/app.db"

    # ===== Protheus =====
    PROTHEUS_DB_URL: str
    # Sufixo do nome físico das tabelas Protheus (Alias + Filial + sufixo).
    PROTHEUS_TABLE_SUFFIX: str = "0"

    # ===== SMTP =====
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    SMTP_USE_TLS: bool = True

    # ===== Admin inicial =====
    SEED_ADMIN_USERNAME: str = "admin"
    SEED_ADMIN_EMAIL: str = "admin@local"
    SEED_ADMIN_PASSWORD: str = "change-me"   # sobreponha via .env em producao

    # ===== Scheduler =====
    SCHEDULER_INTERVAL_MINUTES: int = 60
    REPORTS_OUTPUT_DIR: str = "./reports_output"

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent.parent / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

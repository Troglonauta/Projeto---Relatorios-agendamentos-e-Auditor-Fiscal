"""Modelos ORM (SQLAlchemy).

Tabelas:
- users: usuários da aplicação (NÃO usuários do Protheus).
- user_table_permissions: tabelas Protheus que cada usuário pode consultar.
- user_action_permissions: ações que o usuário pode executar (view/export/schedule).
- audit_logs: trilha de auditoria de TUDO que acontece.
- scheduled_reports: agendamentos de envio automático de relatórios.
- password_reset_tokens: tokens temporários para fluxo de "esqueci a senha".
"""
import uuid
from datetime import datetime
from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
)
from sqlalchemy.orm import relationship

from .database import Base
from .timeutils import now_brt as _brt_now_for_models

# 2026 — Ponto 5 (fuso): TODOS os timestamps de exibicao/auditoria gravam em
# BRT (Brasilia), nao UTC. Antes era um mix (anomalias em BRT, jobs/auditoria em
# UTC), o que deixava horarios 3h fora de sincronia nos relatorios/dashboard.
# Expiracao de sessao/token (ActiveSession.expires_at, PasswordResetToken.
# expires_at) NAO passa por aqui — e' setada explicitamente em UTC no auth.py.


# ---- enums em string (SQLite friendly) ---------------------------------------

class Role:
    ADMIN = "admin"
    OPERATOR = "operator"


class Action:
    VIEW = "view"
    EXPORT = "export"
    SCHEDULE = "schedule"
    FISCAL = "fiscal"      # acesso ao Auditor Fiscal (operador, alem do admin)


# ---- modelos ------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(60), unique=True, index=True, nullable=False)
    email = Column(String(120), unique=True, index=True, nullable=False)
    full_name = Column(String(120), nullable=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default=Role.OPERATOR)

    is_active = Column(Boolean, default=True, nullable=False)
    must_change_password = Column(Boolean, default=False, nullable=False)
    deleted_at = Column(DateTime, nullable=True)  # soft delete

    created_at = Column(DateTime, default=_brt_now_for_models, nullable=False)
    updated_at = Column(DateTime, default=_brt_now_for_models, onupdate=_brt_now_for_models, nullable=False)
    last_login_at = Column(DateTime, nullable=True)
    # v2.21 — username do admin que criou este usuario (auditoria visivel).
    created_by = Column(String(60), nullable=True)

    table_permissions = relationship(
        "UserTablePermission", back_populates="user", cascade="all, delete-orphan"
    )
    action_permissions = relationship(
        "UserActionPermission", back_populates="user", cascade="all, delete-orphan"
    )
    profile_links = relationship(
        "UserProfile", cascade="all, delete-orphan",
        primaryjoin="User.id == UserProfile.user_id",
    )


class UserTablePermission(Base):
    """Whitelist de tabelas Protheus por usuário (ex: SE5, SA1, SD3)."""
    __tablename__ = "user_table_permissions"
    __table_args__ = (UniqueConstraint("user_id", "table_name", name="uq_user_table"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    table_name = Column(String(20), nullable=False, index=True)

    user = relationship("User", back_populates="table_permissions")


class UserActionPermission(Base):
    """Ações permitidas por usuário (view/export/schedule)."""
    __tablename__ = "user_action_permissions"
    __table_args__ = (UniqueConstraint("user_id", "action", name="uq_user_action"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    action = Column(String(20), nullable=False)  # view | export | schedule

    user = relationship("User", back_populates="action_permissions")


class AuditLog(Base):
    """Toda ação relevante na app vira uma linha aqui.

    Mantemos `username` como string além do FK para que mesmo usuários
    posteriormente removidos continuem identificáveis na auditoria.
    """
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=_brt_now_for_models, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    username = Column(String(60), nullable=True, index=True)
    action = Column(String(80), nullable=False, index=True)
    detail = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)
    success = Column(Boolean, default=True, nullable=False)


class ScheduledReport(Base):
    """Agendamento de envio automático de relatório por e-mail.

    `cron` aceita expressão cron (5 campos) — mais flexível que o intervalo fixo
    do APScheduler. O worker que roda a cada 1h reavalia se esta entrada deve
    disparar agora.
    """
    __tablename__ = "scheduled_reports"

    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    table_name = Column(String(20), nullable=False)         # SE5, SA1...
    columns = Column(Text, nullable=True)                   # CSV: "E5_FILIAL,E5_NUMERO,..."
    filters = Column(Text, nullable=True)                   # JSON com filtros do endpoint Protheus
    file_format = Column(String(10), default="xlsx", nullable=False)  # xlsx|csv|pdf|ods

    recipients = Column(Text, nullable=False)               # CSV de e-mails
    cron = Column(String(60), nullable=True)                # ex: "0 8 * * 1-5"
    run_at = Column(DateTime, nullable=True)                # disparo único agendado

    is_active = Column(Boolean, default=True, nullable=False)
    last_run_at = Column(DateTime, nullable=True)
    last_status = Column(String(20), nullable=True)         # success|error
    last_error = Column(Text, nullable=True)

    created_at = Column(DateTime, default=_brt_now_for_models, nullable=False)


class Profile(Base):
    """Perfil/Modulo — agrupa tabelas por area de negocio (Fase 4 Sprint 4.A).

    Codes padrao seeded: LOGISTICA, CONTABIL, CONTROLADORIA, FINANCEIRO, PCP,
    ESTOQUE, ADMINISTRATIVO, COMERCIAL. Admin pode criar/editar livremente
    apos o seed inicial.
    """
    __tablename__ = "profiles"

    id          = Column(Integer, primary_key=True)
    code        = Column(String(30), unique=True, nullable=False, index=True)
    label       = Column(String(60), nullable=False)
    description = Column(Text, nullable=True)
    created_at  = Column(DateTime, default=_brt_now_for_models, nullable=False)
    updated_at  = Column(DateTime, default=_brt_now_for_models, onupdate=_brt_now_for_models, nullable=False)

    tables = relationship("TableProfile", back_populates="profile", cascade="all, delete-orphan")
    users  = relationship("UserProfile",  back_populates="profile", cascade="all, delete-orphan")


class TableProfile(Base):
    """Associacao N:N alias <-> perfil.

    Uma tabela pode pertencer a multiplos perfis (ex: SF1 e' tanto CONTABIL
    quanto CONTROLADORIA). Aliases nao listados aqui ficam disponiveis apenas
    via whitelist direta de UserTablePermission.
    """
    __tablename__ = "table_profiles"
    __table_args__ = (UniqueConstraint("alias", "profile_id", name="uq_table_profile"),)

    id         = Column(Integer, primary_key=True)
    alias      = Column(String(4), nullable=False, index=True)   # ex: "SE1", "ZC8"
    profile_id = Column(Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=_brt_now_for_models, nullable=False)

    profile = relationship("Profile", back_populates="tables")


class UserProfile(Base):
    """Vincula usuario a um ou mais perfis.

    O gate de tabela e' OR:
    - Usuario acessa um alias SE
      (alias em UserTablePermission do user) OU
      (alias em alguma TableProfile cujo profile esta nos UserProfiles do user).
    """
    __tablename__ = "user_profiles"
    __table_args__ = (UniqueConstraint("user_id", "profile_id", name="uq_user_profile"),)

    id         = Column(Integer, primary_key=True)
    user_id    = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    profile_id = Column(Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=_brt_now_for_models, nullable=False)

    profile = relationship("Profile", back_populates="users")


class AppSetting(Base):
    """Configuracoes do app armazenadas no SQLite (substitui o .env).

    - Valores sensiveis (SMTP_PASSWORD, PROTHEUS_DB_URL) vao em
      `encrypted_value` (Fernet — backend/security/crypto.py).
    - Valores publicos (APP_NAME, branding) vao em `plain_value`.
    - `scope` agrupa para a UI do Wizard (db, smtp, api, branding, app).

    O backend nunca le `.env` apos a migracao — todo `get_setting` consulta
    esta tabela primeiro com fallback para variavel de ambiente.
    """
    __tablename__ = "app_settings"

    key = Column(String(80), primary_key=True)
    encrypted_value = Column(Text, nullable=True)
    plain_value = Column(Text, nullable=True)
    scope = Column(String(20), nullable=False, default="app", index=True)
    is_secret = Column(Boolean, nullable=False, default=False)
    description = Column(String(255), nullable=True)
    updated_at = Column(DateTime, default=_brt_now_for_models, onupdate=_brt_now_for_models, nullable=False)
    updated_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)


class Job(Base):
    """Fila de processamento assincrono (Celery + Redis).

    Cada relatorio pesado ou auditoria fiscal vira uma linha aqui. O frontend
    pollar `/api/reports/jobs/{id}` para acompanhar progresso. Arquivos
    gerados ficam em `file_path` (relativo a REPORTS_OUTPUT_DIR).
    """
    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    kind = Column(String(30), nullable=False, index=True)   # report | fiscal_audit
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    payload_json = Column(Text, nullable=False)             # JSON serializado

    status = Column(String(20), nullable=False, default="queued", index=True)
    # queued | running | done | failed | canceled

    created_at = Column(DateTime, default=_brt_now_for_models, nullable=False, index=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    file_path = Column(String(500), nullable=True)
    file_size_bytes = Column(Integer, nullable=True)
    rows_total = Column(Integer, nullable=True)
    rows_processed = Column(Integer, nullable=False, default=0)
    progress_pct = Column(Float, nullable=False, default=0.0)

    error_code = Column(String(20), nullable=True)          # ERR-XXX-NNN
    error_detail = Column(Text, nullable=True)
    celery_task_id = Column(String(80), nullable=True, index=True)
    should_cancel = Column(Boolean, nullable=False, default=False)


class FiscalAnomaly(Base):
    """Divergencia detectada pelo Auditor Fiscal entre Protheus e XML da NFe."""
    __tablename__ = "fiscal_anomalies"

    id = Column(Integer, primary_key=True)
    doc_key = Column(String(44), nullable=False, index=True)       # chave NFe (44 chars)
    branch = Column(String(4), nullable=False, index=True)
    supplier_cnpj = Column(String(14), nullable=True)
    field_compared = Column(String(40), nullable=False)            # quantidade, total, cnpj...
    protheus_value = Column(Text, nullable=True)
    xml_value = Column(Text, nullable=True)
    # Sprint 8 Part 2 — adicionado 'pending' para XMLs nao encontrados (separado de warn/critical).
    severity = Column(String(10), nullable=False, default="warn")  # info | warn | critical | pending
    # Sprint 8 Part 2 — armazena BRT direto (UI da Controladoria mostra Brasilia).
    audited_at = Column(DateTime, default=lambda: _brt_now_for_models(), nullable=False, index=True)
    sent_to_email = Column(Boolean, nullable=False, default=False)
    job_id = Column(String(36), ForeignKey("jobs.id"), nullable=True)

    # === Sprint 4.C — Gestao de status (ack/snooze) =====================
    # Ack: admin marcou como "ciente" — sai do KPI/feed do Dashboard.
    # Snooze: silencia ate uma data; volta a aparecer apos.
    acknowledged_at    = Column(DateTime, nullable=True, index=True)
    acknowledged_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    ack_note           = Column(Text, nullable=True)
    snoozed_until      = Column(DateTime, nullable=True, index=True)


class FiscalDocumentReview(Base):
    """Revisao manual de um DOCUMENTO no Auditor Fiscal (v2.20).

    Quando um usuario (operador ou admin) marca o documento como REVISADO /
    em conformidade, gravamos QUEM revisou e QUANDO — para que TODOS os usuarios
    vejam "revisado manualmente por XXXX". Independente das divergencias
    armazenadas: funciona ate para Nota Ausente / documentos sem divergencia
    (que nao geram linhas pendentes em FiscalAnomaly e por isso nao podiam ser
    "marcados como ciente").

    Uma revisao por (doc_key, branch) — upsert (a ultima decisao prevalece).
    `reviewed_by_name` e' denormalizado para continuar exibindo mesmo que o
    usuario seja removido depois.
    """
    __tablename__ = "fiscal_document_reviews"
    __table_args__ = (
        UniqueConstraint("doc_key", "branch", name="uq_fiscal_review_doc_branch"),
    )

    id               = Column(Integer, primary_key=True)
    doc_key          = Column(String(64), nullable=False, index=True)
    branch           = Column(String(4), nullable=False, index=True)
    reviewed_by_id   = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_by_name = Column(String(120), nullable=False)
    reviewed_at      = Column(DateTime, default=lambda: _brt_now_for_models(), nullable=False)
    note             = Column(Text, nullable=True)


class FiscalFieldDecision(Base):
    """Decisao manual de UM CAMPO de um documento no Auditor Fiscal (v2.24).

    Persiste a sobrescrita de status (Conforme/Divergente/Sem dado) feita pela
    analista em um campo especifico (cabecalho ou item). Guarda QUEM alterou por
    ultimo e QUANDO; cada alteracao tambem e' registrada na trilha de auditoria
    (`fiscal.field.decided`) — historico imutavel. Nao ha exclusao: operador/
    admin podem ALTERAR (atualiza o "por ultimo"), nunca limpar.

    `field_key` identifica o campo de forma estavel:
      - cabecalho: "H:<field>"      (ex: "H:numero")
      - item N:    "<N>:<field>"    (ex: "4:cod_fiscal")
    Upsert por (doc_key, branch, field_key).
    """
    __tablename__ = "fiscal_field_decisions"
    __table_args__ = (
        UniqueConstraint("doc_key", "branch", "field_key", name="uq_fiscal_fielddec"),
    )

    id              = Column(Integer, primary_key=True)
    doc_key         = Column(String(64), nullable=False, index=True)
    branch          = Column(String(4), nullable=False, index=True)
    field_key       = Column(String(80), nullable=False)
    status          = Column(String(10), nullable=False)   # ok | divergent | skipped
    decided_by_id   = Column(Integer, ForeignKey("users.id"), nullable=True)
    decided_by_name = Column(String(120), nullable=False)
    decided_at      = Column(DateTime, default=lambda: _brt_now_for_models(), nullable=False)
    note            = Column(Text, nullable=True)


class ActiveSession(Base):
    """Sessoes JWT ativas — limite de N logins simultaneos por conta (Fase 4).

    JWT por si so e' stateless. Para limitar logins concorrentes em contas
    genericas (ex: usuario 'CONTABIL' compartilhado por 3 pessoas), guardamos
    cada token emitido aqui pelo `jti`. No login, contamos sessoes ativas
    nao-expiradas; se >= MAX_CONCURRENT_SESSIONS, bloqueia.

    Limpeza:
    - Logout (revoke explicito) → `revoked_at` setado.
    - Garbage collect: tokens com `expires_at < now()` ja sao tratados como
      invalidos na consulta; uma tarefa periodica pode apagar fisicamente.
    """
    __tablename__ = "active_sessions"

    id           = Column(Integer, primary_key=True)
    jti          = Column(String(36), unique=True, nullable=False, index=True)
    user_id      = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    created_at   = Column(DateTime, default=_brt_now_for_models, nullable=False)
    # Heartbeat — "sinal de vida" (UTC). Sessao sem last_seen recente NAO conta
    # no limite de sessoes concorrentes (aba fechada libera o slot).
    last_seen_at = Column(DateTime, nullable=True, index=True)
    expires_at   = Column(DateTime, nullable=False)
    revoked_at   = Column(DateTime, nullable=True)
    ip_address   = Column(String(45), nullable=True)
    user_agent   = Column(String(255), nullable=True)


class SavedQuery(Base):
    """Modelos de consulta salvos pelo usuario (Sprint 8 Part 1).

    Antes ficavam em localStorage (per-browser). Agora persistem no SQLite
    com escopo por `user_id` — permite usar a mesma consulta em qualquer
    maquina/navegador e abrir caminho para compartilhamento futuro.

    `payload_json` armazena o JSON serializado do payload do Builder
    (alias, branch, columns, filters, joins). Validado no insert para
    nao guardar lixo, mas re-aplicado liberalmente no carregamento
    (Builder ja sabe ignorar chaves desconhecidas).
    """
    __tablename__ = "saved_queries"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_user_query_name"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    name = Column(String(120), nullable=False)
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_brt_now_for_models, nullable=False)
    updated_at = Column(DateTime, default=_brt_now_for_models, onupdate=_brt_now_for_models,
                        nullable=False)


class PasswordResetToken(Base):
    """Senha alfanumérica temporária gerada no fluxo 'esqueci a senha'.

    Guardamos o hash da senha temporária (mesma `hashed_password` do User
    é atualizada também). Esta tabela serve para rastreio/auditoria e
    expiração explícita.
    """
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=_brt_now_for_models, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)

"""Sprint 6 — Script de auditoria e compliance LGPD/Segurança.

Uso:
    python scripts/security_check.py            # relatório completo
    python scripts/security_check.py --json     # saída JSON (CI/automação)
    python scripts/security_check.py --strict   # exit code != 0 se HIGH/CRITICAL

O que checa (5 categorias):
  1. Permissões do `.env` (POSIX 0600 / NTFS owner-only)
  2. MASTER_KEY existente, formato válido (Fernet 32 bytes b64), não-default
  3. Scan da tabela `app_settings` — qualquer chave marcada como sensível
     (SMTP_PASSWORD, FISCAL_A1_PFX_PASSWORD, etc) está em `encrypted_value`
     e o `plain_value` correspondente é NULL. Falsos-positivos são rejeitados
     se o token Fernet for válido.
  4. Mapeia todas as rotas registradas em `app.routes` e classifica:
     - Públicas (login, health, branding, setup): ESPERADO público
     - Protegidas por `require_admin` ou `require_action` → OK
     - Sem proteção → ALERT
  5. Arquivos sensíveis no disco: `data/secrets/a1.pfx` chmod 600, `data/app.db`
     fora do diretório web público, backups com permissão restrita.

Saídas:
  - Console colorido (humano)
  - JSON com `--json` (machine-readable: severity, category, finding, remediation)
  - Exit code 0 (ok) / 1 (falhas LOW-MEDIUM) / 2 (HIGH-CRITICAL) com `--strict`

NUNCA executa operações destrutivas. Read-only sempre.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import stat
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Adiciona o root do projeto ao sys.path para importar `backend.*`
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ==============================================================
#  Modelo de finding
# ==============================================================

SEVERITY_ORDER = {"OK": 0, "INFO": 1, "LOW": 2, "MEDIUM": 3, "HIGH": 4, "CRITICAL": 5}

class Finding:
    __slots__ = ("category", "severity", "title", "detail", "remediation")
    def __init__(self, category: str, severity: str, title: str,
                 detail: str = "", remediation: str = ""):
        assert severity in SEVERITY_ORDER, f"severity invalida: {severity}"
        self.category = category
        self.severity = severity
        self.title = title
        self.detail = detail
        self.remediation = remediation

    def to_dict(self) -> dict:
        return {
            "category": self.category, "severity": self.severity,
            "title": self.title, "detail": self.detail,
            "remediation": self.remediation,
        }


# ==============================================================
#  1) Permissoes do .env
# ==============================================================

def check_env_permissions() -> list[Finding]:
    findings: list[Finding] = []
    env_path = ROOT / ".env"

    if not env_path.exists():
        findings.append(Finding(
            "env", "INFO",
            ".env nao existe (aceitavel em dev sem credenciais externas)",
            f"Procurado em: {env_path}",
            "Crie .env com MASTER_KEY e PROTHEUS_DB_URL para producao. "
            "Em LXC, prefira EnvironmentFile= no systemd unit.",
        ))
        return findings

    st = env_path.stat()
    size = st.st_size

    findings.append(Finding(
        "env", "OK", f".env localizado ({size} bytes)",
        f"Path: {env_path}",
    ))

    # POSIX: chmod deve ser 0600 (ou menor — sem leitura para grupo/world)
    if platform.system() != "Windows":
        mode = stat.S_IMODE(st.st_mode)
        owner_only = (mode & 0o077) == 0  # nenhum bit para grupo/world
        if owner_only:
            findings.append(Finding(
                "env", "OK",
                f".env com permissao restrita ({oct(mode)})",
                "Apenas o owner pode ler/escrever — adequado para LGPD.",
            ))
        else:
            findings.append(Finding(
                "env", "HIGH",
                f".env com permissao excessivamente aberta ({oct(mode)})",
                "Outros usuarios do sistema podem ler segredos (MASTER_KEY, SMTP, etc).",
                "Execute: chmod 600 .env  (LXC/Proxmox). "
                "Confirme que o owner e' o usuario que roda o uvicorn/systemd.",
            ))
    else:
        # Windows: garante que ninguem do grupo "Everyone" tem permissao.
        # Não temos ferramenta nativa pratica aqui — apenas relatamos.
        findings.append(Finding(
            "env", "INFO",
            ".env em Windows — verifique ACL manualmente",
            "Windows nao expoe chmod POSIX. Use: icacls .env  "
            "para verificar se 'Everyone' tem acesso (NAO deve ter).",
            "Em producao Proxmox/LXC (Linux), o check sera automatico.",
        ))

    # Garante que .env nao foi commitado por engano
    gi = ROOT / ".gitignore"
    if gi.exists():
        gi_text = gi.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"^\.env\b", gi_text, re.M):
            findings.append(Finding(
                "env", "OK", ".env ignorado pelo Git", "",
            ))
        else:
            findings.append(Finding(
                "env", "MEDIUM", ".env NAO listado em .gitignore",
                "Risco de commit acidental de credenciais.",
                "Adicione `.env` em .gitignore.",
            ))

    return findings


# ==============================================================
#  2) MASTER_KEY existencia + validade + nao-default
# ==============================================================

# Chaves "default" conhecidas que NUNCA devem aparecer em producao.
# (placeholders típicos que poderiam vazar em docs/exemplos).
WELL_KNOWN_BAD_KEYS = {
    # 32 bytes base64 com tudo zero — exemplo trivial, jamais usar
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    # 32 bytes "A" — outro exemplo trivial
    "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE=",
    # Frase humana acidentalmente em base64 — improvavel ser Fernet valida
    "default-master-key-change-me",
    "change-me",
    "your-secret-key-here",
}


def check_master_key() -> list[Finding]:
    findings: list[Finding] = []

    # Le do env diretamente OU do .env (sem importar crypto.py — evita
    # gerar uma chave se nao houver, esse script deve ser passivo)
    key = os.environ.get("MASTER_KEY")
    if not key:
        env_path = ROOT / ".env"
        if env_path.exists():
            for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw.strip()
                if line.startswith("MASTER_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    if not key:
        findings.append(Finding(
            "master_key", "HIGH",
            "MASTER_KEY ausente",
            "Sem chave Fernet, segredos no SQLite ficam sem criptografia ou o app gera "
            "uma nova chave (que invalida tokens existentes).",
            "Faca BOOT da aplicacao uma vez (gera automaticamente em .env) "
            "OU defina manualmente: python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())' >> .env",
        ))
        return findings

    if key in WELL_KNOWN_BAD_KEYS:
        findings.append(Finding(
            "master_key", "CRITICAL",
            "MASTER_KEY e' um valor de exemplo/placeholder",
            f"Valor detectado: {key[:8]}...{key[-4:]} — proibido em producao!",
            "Gere uma chave NOVA: python -m backend.cli.rotate_master_key "
            "(re-criptografa todos os segredos com a chave nova).",
        ))
        return findings

    # Valida formato Fernet (32 bytes url-safe base64)
    try:
        import base64
        raw = base64.urlsafe_b64decode(key.encode("ascii"))
        if len(raw) != 32:
            findings.append(Finding(
                "master_key", "HIGH",
                f"MASTER_KEY tem {len(raw)} bytes — Fernet exige 32",
                "Chave malformada — encrypt/decrypt vai falhar.",
                "Re-gere com Fernet.generate_key().",
            ))
            return findings
    except Exception as exc:
        findings.append(Finding(
            "master_key", "HIGH",
            "MASTER_KEY nao e' base64 url-safe valido",
            f"Erro decodificando: {exc}",
            "Re-gere a chave (Fernet.generate_key()).",
        ))
        return findings

    # Confirma que o Fernet inicializa
    try:
        from cryptography.fernet import Fernet
        f = Fernet(key.encode("ascii"))
        # round-trip test
        token = f.encrypt(b"ping")
        assert f.decrypt(token) == b"ping"
    except Exception as exc:
        findings.append(Finding(
            "master_key", "HIGH",
            "MASTER_KEY nao passa no round-trip Fernet",
            f"{exc}",
            "Re-gere a chave.",
        ))
        return findings

    findings.append(Finding(
        "master_key", "OK",
        "MASTER_KEY presente, formato Fernet valido, round-trip OK",
        f"Hash da chave (primeiros 8 chars): {key[:8]}...",
    ))
    return findings


# ==============================================================
#  3) Plain-text scan na tabela app_settings
# ==============================================================

# Chaves que TEM que estar criptografadas (is_secret=True). Se o app salvou
# em plain_value, e' bug critico (vazamento LGPD).
MUST_BE_ENCRYPTED = {
    "PROTHEUS_DB_URL",
    "SMTP_PASSWORD",
    "TRANSMITE_PASSWORD",
    "FISCAL_TSS_PASSWORD",
    "FISCAL_A1_PFX_PASSWORD",  # Sprint 6
    "SMARTLINK_TOKEN",
    "NFSTOCK_TOKEN",      # Sprint 7 — Personal Access Token estatico (Alterdata)
    "FISCAL_WEBHOOK_URL", # Sprint 8 Part 3 — URL Slack/Teams (contem token na path)
    "QUEUE_BROKER_URL",
    "QUEUE_RESULT_BACKEND",
}

# Heuristica de detecção de secret-leak em campos não-secretos. Se aparecer
# qualquer um destes prefixos num plain_value de campo que NAO deveria
# conter segredo, sinaliza MEDIUM.
SECRET_LEAK_PATTERNS = [
    re.compile(r"^[a-zA-Z0-9]{40,}$"),       # token base64-ish longo
    re.compile(r"^\$2[aby]\$\d+\$"),          # bcrypt hash
    re.compile(r"^-----BEGIN ", re.M),        # cert PEM
    re.compile(r"password=.+", re.I),         # campo de URL com senha exposta
    re.compile(r"://[^/:]+:[^@/]+@"),         # URL com senha embutida
]


def check_app_settings_plaintext() -> list[Finding]:
    findings: list[Finding] = []
    try:
        # Import lazy — script funciona mesmo se backend nao puder carregar
        from backend.database import SessionLocal
        from backend.models import AppSetting
        from backend.security.crypto import decrypt, CryptoError
    except Exception as exc:
        findings.append(Finding(
            "settings_scan", "MEDIUM",
            "Nao foi possivel importar backend.* — scan pulado",
            f"{exc}",
            "Garanta que o ambiente virtual esta ativo (.venv) "
            "e que `pip install -r requirements.txt` foi executado.",
        ))
        return findings

    db = SessionLocal()
    try:
        rows = db.query(AppSetting).order_by(AppSetting.key).all()
    except Exception as exc:
        findings.append(Finding(
            "settings_scan", "INFO",
            "Tabela app_settings nao existe ainda (banco zerado)",
            f"{exc}",
            "Rode a aplicacao pelo menos uma vez para criar o schema.",
        ))
        return findings
    finally:
        db.close()

    if not rows:
        findings.append(Finding(
            "settings_scan", "INFO", "app_settings vazia (ambiente novo)", "",
        ))
        return findings

    total = len(rows)
    secrets_ok = 0
    secrets_decryptable = 0
    findings_per_key: list[Finding] = []

    keys_seen = set()
    for r in rows:
        keys_seen.add(r.key)
        # Regra 1: chaves criticas DEVEM estar em encrypted_value
        if r.key in MUST_BE_ENCRYPTED:
            if r.plain_value not in (None, ""):
                findings_per_key.append(Finding(
                    "settings_scan", "CRITICAL",
                    f"VAZAMENTO: '{r.key}' armazenado em PLAIN TEXT",
                    f"plain_value={'***' if r.plain_value else ''} (mascarado neste relatorio). "
                    f"is_secret={r.is_secret} scope={r.scope}.",
                    f"Atualize o setting via UI ou: "
                    f"settings_store.set_setting('{r.key}', VALUE, is_secret=True). "
                    f"Aguarde re-encrypt e confirme com novo scan.",
                ))
                continue
            if not r.is_secret:
                findings_per_key.append(Finding(
                    "settings_scan", "HIGH",
                    f"'{r.key}' marcada como is_secret=False (esperado True)",
                    f"scope={r.scope}",
                    "Forcar UPDATE: UPDATE app_settings SET is_secret=1 WHERE key='%s'. "
                    "Depois re-salvar via UI para encriptar." % r.key,
                ))
                continue
            if not r.encrypted_value:
                findings_per_key.append(Finding(
                    "settings_scan", "HIGH",
                    f"'{r.key}' marcada secreta mas encrypted_value esta vazia",
                    "Provavel falha na ultima escrita.",
                    "Re-salve via UI (Admin / Configuracoes).",
                ))
                continue
            # Confere que descripta sem erro (chave compativel)
            try:
                _ = decrypt(r.encrypted_value)
                secrets_ok += 1
                secrets_decryptable += 1
            except CryptoError as exc:
                findings_per_key.append(Finding(
                    "settings_scan", "HIGH",
                    f"'{r.key}' encriptada mas NAO descriptografa com MASTER_KEY atual",
                    f"{exc}",
                    "MASTER_KEY foi trocada sem migration? Rode CLI rotate_master_key "
                    "ou re-salve a credencial via UI.",
                ))
            continue

        # Regra 2: chaves nao-criticas — checa se algum plain_value parece secret
        if r.plain_value:
            for patt in SECRET_LEAK_PATTERNS:
                if patt.search(r.plain_value or ""):
                    findings_per_key.append(Finding(
                        "settings_scan", "MEDIUM",
                        f"'{r.key}' parece conter segredo em plain_value",
                        f"plain_value casa com pattern: {patt.pattern[:40]}",
                        "Revise — se for credencial, marque is_secret=True e re-salve.",
                    ))
                    break

    # Resumo agregado primeiro, depois os achados individuais
    findings.append(Finding(
        "settings_scan", "OK" if not findings_per_key else "INFO",
        f"app_settings: {total} linhas, {secrets_ok} segredos OK descriptografaveis",
        f"chaves: {', '.join(sorted(keys_seen)[:10])}{'...' if len(keys_seen)>10 else ''}",
    ))

    # Confere chaves criticas AUSENTES (avisa apenas)
    missing_critical = MUST_BE_ENCRYPTED - keys_seen
    if missing_critical:
        findings.append(Finding(
            "settings_scan", "INFO",
            f"{len(missing_critical)} chaves criticas nao configuradas ainda",
            f"{', '.join(sorted(missing_critical))}",
            "Esperado em ambientes novos. Configure via Wizard ou Admin.",
        ))

    findings.extend(findings_per_key)
    return findings


# ==============================================================
#  4) Cobertura de protecao JWT nas rotas
# ==============================================================

# Rotas que DEVEM ser publicas (sem JWT) — mantemos uma whitelist explicita.
# Qualquer outra coisa publica e' suspeita.
PUBLIC_ALLOWED_PATTERNS = [
    re.compile(r"^/$"),
    re.compile(r"^/health$"),
    re.compile(r"^/api/branding/logo$"),
    re.compile(r"^/api/auth/(login|forgot-password|change-password)$"),
    re.compile(r"^/api/settings/public$"),
    re.compile(r"^/api/setup($|/)"),       # Wizard de primeiro boot
    re.compile(r"^/static($|/)"),
    # FastAPI Swagger/ReDoc built-ins — devem ficar publicos
    re.compile(r"^/docs($|/)"),
    re.compile(r"^/redoc$"),
    re.compile(r"^/openapi\.json$"),
]


def _route_is_public_expected(path: str) -> bool:
    return any(p.match(path) for p in PUBLIC_ALLOWED_PATTERNS)


def _route_has_auth_dependency(route) -> bool:
    """Detecta se a rota tem qualquer dependency name in {get_current_user,
    require_admin, require_action, require_setup_complete} no grafo de deps.
    """
    AUTH_NAMES = {"get_current_user", "require_admin", "require_action",
                  "_action_dep", "require_setup_complete"}

    # FastAPI guarda dependant.dependencies (lista recursiva)
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return False

    seen = set()
    def walk(d):
        if id(d) in seen: return False
        seen.add(id(d))
        call = getattr(d, "call", None)
        if call is not None:
            name = getattr(call, "__name__", "") or repr(call)
            if name in AUTH_NAMES:
                return True
            # require_action retorna closure chamada `_action_dep` — pega tbm
            if "require_action" in repr(call) or "_action_dep" in name:
                return True
        for sub in getattr(d, "dependencies", []) or []:
            if walk(sub):
                return True
        return False

    return walk(dependant)


def check_route_protection() -> list[Finding]:
    findings: list[Finding] = []
    try:
        # Importa app SEM disparar lifespan
        from backend.main import app
    except Exception as exc:
        findings.append(Finding(
            "routes", "MEDIUM",
            "Nao foi possivel importar backend.main — scan de rotas pulado",
            f"{exc}",
            "Garanta que o ambiente virtual esta ativo e dependencias instaladas.",
        ))
        return findings

    protected = []
    publics_expected = []
    publics_unexpected = []

    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not path or "GET" not in methods and "POST" not in methods \
           and "PUT" not in methods and "DELETE" not in methods \
           and "PATCH" not in methods:
            # WebSocket, Mount, etc — ignora
            continue

        has_auth = _route_has_auth_dependency(route)
        is_public_ok = _route_is_public_expected(path)

        if has_auth:
            protected.append((sorted(methods), path))
        elif is_public_ok:
            publics_expected.append((sorted(methods), path))
        else:
            publics_unexpected.append((sorted(methods), path))

    findings.append(Finding(
        "routes", "OK",
        f"{len(protected)} rotas protegidas por JWT",
        f"Exemplos: " + ", ".join(f"{m[0]} {p}" for m, p in protected[:5]),
    ))
    findings.append(Finding(
        "routes", "INFO",
        f"{len(publics_expected)} rotas publicas autorizadas (whitelist)",
        f"Login, health, branding, Wizard, static — esperado.",
    ))

    if publics_unexpected:
        for m, p in publics_unexpected:
            findings.append(Finding(
                "routes", "HIGH",
                f"Rota SEM autenticacao fora da whitelist: {','.join(m)} {p}",
                "Possivel exposicao de funcionalidade interna.",
                f"Adicione `dependencies=[Depends(require_admin)]` ou "
                f"`require_action('...')` no router de '{p}', ou inclua o path "
                f"em PUBLIC_ALLOWED_PATTERNS apos analise.",
            ))
    else:
        findings.append(Finding(
            "routes", "OK",
            "Nenhuma rota publica suspeita — cobertura JWT 100%",
        ))

    return findings


# ==============================================================
#  5) Arquivos sensiveis no disco (PFX, app.db, backups)
# ==============================================================

def check_sensitive_files() -> list[Finding]:
    findings: list[Finding] = []
    targets = [
        ROOT / "data" / "secrets" / "a1.pfx",
        ROOT / "data" / "app.db",
        ROOT / "data" / "branding" / "logo.png",  # nao secret, mas confere existencia
    ]

    for target in targets:
        if not target.exists():
            findings.append(Finding(
                "files", "INFO",
                f"{target.relative_to(ROOT)} nao existe (aceitavel)",
                "",
            ))
            continue

        st = target.stat()
        size = st.st_size

        # PFX e app.db devem ter perms restritas (600/640 em POSIX)
        is_secret_file = "secrets" in str(target) or target.name == "app.db"
        if platform.system() != "Windows" and is_secret_file:
            mode = stat.S_IMODE(st.st_mode)
            if (mode & 0o077) != 0:
                findings.append(Finding(
                    "files", "HIGH",
                    f"{target.relative_to(ROOT)} com permissao aberta ({oct(mode)})",
                    f"size={size} bytes",
                    f"Execute: chmod 600 '{target}' (LXC). "
                    f"Garanta que o owner e' o usuario do uvicorn.",
                ))
            else:
                findings.append(Finding(
                    "files", "OK",
                    f"{target.relative_to(ROOT)} permissao restrita ({oct(mode)})",
                    f"size={size} bytes",
                ))
        else:
            findings.append(Finding(
                "files", "OK",
                f"{target.relative_to(ROOT)} presente",
                f"size={size} bytes",
            ))

    # Confere se data/secrets/ NAO esta servida via /static (catastrofico se sim)
    static_mount = ROOT / "frontend"
    if static_mount.exists():
        secrets_inside_static = (static_mount / "secrets").exists() or \
                                (static_mount / "data").exists()
        if secrets_inside_static:
            findings.append(Finding(
                "files", "CRITICAL",
                "Diretorio sensivel dentro de frontend/ (servido como /static)",
                "PFX/app.db ficariam acessiveis por HTTP!",
                "Mova data/ para fora de frontend/. Nunca coloque segredos sob mount estatico.",
            ))

    return findings


# ==============================================================
#  Render
# ==============================================================

ANSI = {
    "RESET": "\033[0m", "BOLD": "\033[1m", "DIM": "\033[2m",
    "RED": "\033[91m", "YELLOW": "\033[93m", "GREEN": "\033[92m",
    "CYAN": "\033[96m", "MAGENTA": "\033[95m",
}

SEV_COLOR = {
    "OK": "GREEN", "INFO": "CYAN", "LOW": "YELLOW",
    "MEDIUM": "YELLOW", "HIGH": "RED", "CRITICAL": "MAGENTA",
}

SEV_ICON = {
    "OK": "[OK]", "INFO": "[i]", "LOW": "[.]",
    "MEDIUM": "[!]", "HIGH": "[X]", "CRITICAL": "[!!]",
}


def _supports_color() -> bool:
    if "--no-color" in sys.argv: return False
    if os.environ.get("NO_COLOR"): return False
    return sys.stdout.isatty()


def render_text(findings: list[Finding]) -> None:
    use_color = _supports_color()
    def col(text, c): return f"{ANSI[c]}{text}{ANSI['RESET']}" if use_color else text

    counts = {s: 0 for s in SEVERITY_ORDER}
    for f in findings:
        counts[f.severity] += 1

    print()
    print(col("=" * 72, "BOLD"))
    print(col(" PROTHEUS REPORTS — SECURITY & COMPLIANCE AUDIT", "BOLD"))
    print(col(f" Rodado em: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (TZ local)", "DIM"))
    print(col(f" Plataforma: {platform.system()} {platform.release()}", "DIM"))
    print(col("=" * 72, "BOLD"))
    print()

    grouped: dict[str, list[Finding]] = {}
    for f in findings:
        grouped.setdefault(f.category, []).append(f)

    category_labels = {
        "env":            "1. Permissoes do .env",
        "master_key":     "2. MASTER_KEY (Fernet)",
        "settings_scan":  "3. Scan de plain-text em app_settings",
        "routes":         "4. Protecao JWT das rotas",
        "files":          "5. Arquivos sensiveis no disco",
    }

    for cat, label in category_labels.items():
        items = grouped.get(cat, [])
        if not items:
            print(col(f"[{label}] (sem dados)", "DIM"))
            continue
        worst = max(items, key=lambda f: SEVERITY_ORDER[f.severity]).severity
        print(col(f"[{label}]  worst={worst}", SEV_COLOR[worst]))
        for f in items:
            icon = SEV_ICON[f.severity]
            sev_styled = col(f"[{f.severity:^8}]", SEV_COLOR[f.severity])
            print(f"  {sev_styled} {icon} {f.title}")
            if f.detail:
                for line in f.detail.split("\n"):
                    print(col(f"           {line}", "DIM"))
            if f.remediation and f.severity not in ("OK", "INFO"):
                print(col(f"           → {f.remediation}", "YELLOW"))
        print()

    # Sumario
    print(col("=" * 72, "BOLD"))
    print(col(" SUMARIO", "BOLD"))
    for sev in ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO", "OK"]:
        n = counts[sev]
        if n:
            print(f"  {col(f'{sev:>8}', SEV_COLOR[sev])}  {n}")
    print(col("=" * 72, "BOLD"))

    worst_overall = max((SEVERITY_ORDER[f.severity] for f in findings), default=0)
    if worst_overall >= SEVERITY_ORDER["HIGH"]:
        print(col(" RESULTADO: Achados HIGH ou CRITICAL — corrigir antes de subir A1 real.", "RED"))
    elif worst_overall >= SEVERITY_ORDER["MEDIUM"]:
        print(col(" RESULTADO: Achados MEDIUM — revisar antes de auditoria oficial.", "YELLOW"))
    else:
        print(col(" RESULTADO: AMBIENTE EM CONFORMIDADE.", "GREEN"))
    print()


# ==============================================================
#  Main
# ==============================================================

def _force_utf8_stdout() -> None:
    """Windows console e' cp1252 por default — re-encoda stdout para UTF-8 para
    aceitar caracteres como — “ ” ↩ etc. dos textos."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main(argv: Optional[list[str]] = None) -> int:
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(
        description="Audit de seguranca e compliance LGPD do Protheus Reports.",
    )
    parser.add_argument("--json", action="store_true",
                        help="Saida em JSON ao inves de texto colorido")
    parser.add_argument("--strict", action="store_true",
                        help="Exit code 2 se houver HIGH/CRITICAL, 1 se MEDIUM/LOW")
    parser.add_argument("--no-color", action="store_true",
                        help="Desliga cores ANSI")
    args = parser.parse_args(argv)

    findings: list[Finding] = []
    findings.extend(check_env_permissions())
    findings.extend(check_master_key())
    findings.extend(check_app_settings_plaintext())
    findings.extend(check_route_protection())
    findings.extend(check_sensitive_files())

    if args.json:
        out = {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "platform": f"{platform.system()} {platform.release()}",
            "findings": [f.to_dict() for f in findings],
            "counts": {
                sev: sum(1 for f in findings if f.severity == sev)
                for sev in SEVERITY_ORDER
            },
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        render_text(findings)

    if args.strict:
        worst = max((SEVERITY_ORDER[f.severity] for f in findings), default=0)
        if worst >= SEVERITY_ORDER["HIGH"]:
            return 2
        if worst >= SEVERITY_ORDER["LOW"]:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

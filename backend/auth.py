"""Autenticação: hashing bcrypt, JWT, geração de senha temporária.

Funções aqui são puras (sem efeito colateral em DB) — quem persiste é o router.
"""
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from .config import get_settings

settings = get_settings()

# bcrypt: rounds default (12) já é seguro. Truncamos manualmente em 72 bytes
# (limite do algoritmo) para evitar erro silencioso em senhas muito longas.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---- Senhas -------------------------------------------------------------------

def hash_password(plain: str) -> str:
    return pwd_context.hash(plain[:72])


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(plain[:72], hashed)
    except Exception:
        return False


def generate_temp_password(length: int = 12) -> str:
    """Senha alfanumérica forte para o fluxo 'esqueci a senha'.

    Mistura letras maiúsculas, minúsculas e dígitos. Sem caracteres especiais
    para evitar confusão do usuário ao digitar.
    """
    alphabet = string.ascii_letters + string.digits
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        # Garante ao menos 1 maiúscula, 1 minúscula e 1 dígito.
        if (any(c.islower() for c in pwd)
                and any(c.isupper() for c in pwd)
                and any(c.isdigit() for c in pwd)):
            return pwd


# ---- JWT ----------------------------------------------------------------------

def create_access_token(
    subject: str, extra_claims: Optional[dict] = None,
) -> tuple[str, str, datetime]:
    """Cria JWT com `jti` (JWT ID) para rastreamento de sessao concorrente.

    Retorna `(token, jti, expires_at_utc)` — o caller persiste em ActiveSession.
    """
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    jti = str(uuid.uuid4())
    payload = {
        "sub": subject,
        "iat": now,
        "exp": exp,
        "jti": jti,
    }
    if extra_claims:
        payload.update(extra_claims)
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    # expires_at em UTC naive para gravar no SQLite (consistente com `utcnow()`)
    return token, jti, exp.replace(tzinfo=None)


def decode_access_token(token: str) -> dict:
    """Lança JWTError se inválido/expirado — quem chama trata."""
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])

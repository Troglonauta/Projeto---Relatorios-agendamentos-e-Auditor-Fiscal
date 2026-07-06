"""Sprint 8 Part 1 — Consultas Salvas do Builder (per-user, server-side).

Substitui o storage `localStorage` da Sprint 8 anterior. Cada modelo guarda
o JSON cru do payload do Builder; o frontend e' liberal ao reaplicar (chaves
desconhecidas sao ignoradas), entao novos campos no payload nao quebram
modelos antigos.

Endpoints (todos exigem login; nao precisa ser admin):
- GET    /api/saved-queries          lista os meus
- POST   /api/saved-queries          cria/substitui por (user_id, name)
- PUT    /api/saved-queries/{id}     atualiza payload + nome (renomeia)
- DELETE /api/saved-queries/{id}     remove

Limites:
- Tamanho do payload: 32 KB (suficiente para dezenas de colunas/joins)
- Maximo de modelos por usuario: 100 (defensivo — evita DOS-by-spam)
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import SavedQuery, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/saved-queries", tags=["saved-queries"])

MAX_PAYLOAD_BYTES = 32 * 1024     # 32 KB
MAX_MODELS_PER_USER = 100


class SavedQueryIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    payload: dict = Field(default_factory=dict)


def _to_out(q: SavedQuery) -> dict:
    try:
        payload = json.loads(q.payload_json) if q.payload_json else {}
    except json.JSONDecodeError:
        # Payload corrompido — devolve vazio para nao quebrar o frontend
        logger.warning("SavedQuery #%s tem payload_json invalido", q.id)
        payload = {}
    return {
        "id": q.id,
        "name": q.name,
        "payload": payload,
        "created_at": q.created_at.isoformat() if q.created_at else None,
        "updated_at": q.updated_at.isoformat() if q.updated_at else None,
    }


def _serialize_payload(payload: dict) -> str:
    """Serializa + valida tamanho. Lanca HTTPException 400 se excede."""
    s = json.dumps(payload or {}, ensure_ascii=False)
    if len(s.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Payload excede {MAX_PAYLOAD_BYTES // 1024} KB — reduza colunas/filtros.",
        )
    return s


@router.get("")
def list_my_queries(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Lista todos os modelos do usuario corrente, ordenados pelo mais recente."""
    rows = (
        db.query(SavedQuery)
        .filter(SavedQuery.user_id == user.id)
        .order_by(SavedQuery.updated_at.desc())
        .all()
    )
    return {"total": len(rows), "items": [_to_out(q) for q in rows]}


@router.post("", status_code=status.HTTP_201_CREATED)
def save_query(
    payload: SavedQueryIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cria OU substitui por (user_id, name). Se ja existir com esse nome, atualiza.

    Comportamento "upsert" foi escolhido para casar com a UX antiga (localStorage):
    salvar um modelo com nome existente sobrescreve. Para renomear, use PUT /{id}.
    """
    name = payload.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nome obrigatorio")

    existing = (
        db.query(SavedQuery)
        .filter(SavedQuery.user_id == user.id, SavedQuery.name == name)
        .first()
    )
    payload_str = _serialize_payload(payload.payload)

    if existing:
        existing.payload_json = payload_str
        db.commit()
        db.refresh(existing)
        return _to_out(existing)

    # Cap de quantidade — defensivo contra acumulacao silenciosa
    count = db.query(SavedQuery).filter(SavedQuery.user_id == user.id).count()
    if count >= MAX_MODELS_PER_USER:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Voce ja tem {count} modelos salvos (limite {MAX_MODELS_PER_USER}). "
            f"Apague algum antes de criar novo.",
        )

    row = SavedQuery(
        user_id=user.id, name=name, payload_json=payload_str,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # Race condition rara — outro client salvou com mesmo nome entre o check e o insert
        raise HTTPException(status.HTTP_409_CONFLICT, "Modelo com esse nome ja existe")
    db.refresh(row)
    return _to_out(row)


@router.put("/{query_id}")
def update_query(
    query_id: int,
    payload: SavedQueryIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Atualiza payload e/ou nome de um modelo existente."""
    row = (
        db.query(SavedQuery)
        .filter(SavedQuery.id == query_id, SavedQuery.user_id == user.id)
        .first()
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Modelo nao encontrado")

    new_name = payload.name.strip()
    if not new_name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nome obrigatorio")

    row.name = new_name
    row.payload_json = _serialize_payload(payload.payload)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Ja existe outro modelo com esse nome")
    db.refresh(row)
    return _to_out(row)


@router.delete("/{query_id}")
def delete_query(
    query_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Apaga um modelo do usuario corrente."""
    row = (
        db.query(SavedQuery)
        .filter(SavedQuery.id == query_id, SavedQuery.user_id == user.id)
        .first()
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Modelo nao encontrado")
    db.delete(row)
    db.commit()
    return {"detail": "Modelo apagado", "id": query_id}

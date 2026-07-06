"""Endpoints REST de Perfis/Modulos (Fase 4.A).

Modelo de seguranca:
- Toda rota e' admin-only.
- Operadores nunca chamam estes endpoints diretamente — eles veem o efeito
  via `/api/protheus/aliases` (que filtra pelos perfis do user corrente).

Endpoints:
- GET    /api/profiles                       lista todos
- POST   /api/profiles                       cria perfil novo
- PUT    /api/profiles/{id}                  atualiza label/descricao
- DELETE /api/profiles/{id}                  remove (cascade nas associacoes)
- GET    /api/profiles/{id}/tables           lista aliases do perfil
- POST   /api/profiles/{id}/tables           adiciona um alias
- DELETE /api/profiles/{id}/tables/{alias}   remove um alias
- GET    /api/users/{user_id}/profiles       lista perfis do user
- PUT    /api/users/{user_id}/profiles       substitui perfis do user
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import audit
from ..database import get_db
from ..deps import get_client_ip, require_admin
from ..models import Profile, TableProfile, User, UserProfile
from ..protheus_aliases import is_known_alias
from ..schemas import (
    ProfileCreate, ProfileOut, ProfileUpdate, TableAssign, UserProfilesUpdate,
)

router = APIRouter(prefix="/api/profiles", tags=["profiles"],
                   dependencies=[Depends(require_admin)])

# Router secundario sob /api/users — usado para gerir perfis de um usuario.
user_profiles_router = APIRouter(prefix="/api/users", tags=["profiles"],
                                 dependencies=[Depends(require_admin)])


def _to_out(p: Profile, db: Session) -> ProfileOut:
    aliases = sorted({t.alias for t in p.tables})
    user_count = db.query(func.count(UserProfile.id)).filter(UserProfile.profile_id == p.id).scalar() or 0
    return ProfileOut(
        id=p.id, code=p.code, label=p.label, description=p.description,
        created_at=p.created_at, tables=aliases, user_count=int(user_count),
    )


# ---- CRUD perfis ----------------------------------------------------------

@router.get("", response_model=List[ProfileOut])
def list_profiles(db: Session = Depends(get_db)):
    return [_to_out(p, db) for p in db.query(Profile).order_by(Profile.label).all()]


@router.post("", response_model=ProfileOut, status_code=status.HTTP_201_CREATED)
def create_profile(
    payload: ProfileCreate, request: Request,
    db: Session = Depends(get_db), admin=Depends(require_admin),
):
    code = payload.code.upper().strip()
    if db.query(Profile).filter(Profile.code == code).first():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Perfil '{code}' ja existe")
    p = Profile(code=code, label=payload.label.strip(), description=payload.description)
    db.add(p); db.commit(); db.refresh(p)
    audit.log(db, action="profile.create", user=admin, ip=get_client_ip(request),
              detail=f"code={code}")
    return _to_out(p, db)


@router.put("/{profile_id}", response_model=ProfileOut)
def update_profile(
    profile_id: int, payload: ProfileUpdate, request: Request,
    db: Session = Depends(get_db), admin=Depends(require_admin),
):
    p = db.query(Profile).filter(Profile.id == profile_id).first()
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Perfil nao encontrado")
    if payload.label is not None:
        p.label = payload.label.strip()
    if payload.description is not None:
        p.description = payload.description
    db.commit(); db.refresh(p)
    audit.log(db, action="profile.update", user=admin, ip=get_client_ip(request),
              detail=f"code={p.code}")
    return _to_out(p, db)


@router.delete("/{profile_id}")
def delete_profile(
    profile_id: int, request: Request,
    db: Session = Depends(get_db), admin=Depends(require_admin),
):
    p = db.query(Profile).filter(Profile.id == profile_id).first()
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Perfil nao encontrado")
    code = p.code
    db.delete(p); db.commit()
    audit.log(db, action="profile.delete", user=admin, ip=get_client_ip(request),
              detail=f"code={code}")
    return {"detail": f"Perfil '{code}' removido"}


# ---- Matriz tabela x perfil ----------------------------------------------

@router.get("/{profile_id}/tables")
def list_profile_tables(profile_id: int, db: Session = Depends(get_db)):
    p = db.query(Profile).filter(Profile.id == profile_id).first()
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Perfil nao encontrado")
    return {"profile": p.code, "tables": sorted({t.alias for t in p.tables})}


@router.post("/{profile_id}/tables", status_code=status.HTTP_201_CREATED)
def add_table_to_profile(
    profile_id: int, payload: TableAssign, request: Request,
    db: Session = Depends(get_db), admin=Depends(require_admin),
):
    p = db.query(Profile).filter(Profile.id == profile_id).first()
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Perfil nao encontrado")
    alias = payload.alias.upper().strip()
    if not is_known_alias(alias):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Alias '{alias}' nao esta no catalogo Protheus")
    if db.query(TableProfile).filter(
        TableProfile.profile_id == profile_id, TableProfile.alias == alias
    ).first():
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Alias '{alias}' ja associado a este perfil")
    db.add(TableProfile(alias=alias, profile_id=profile_id))
    db.commit()
    audit.log(db, action="profile.table.add", user=admin, ip=get_client_ip(request),
              detail=f"profile={p.code} alias={alias}")
    return {"detail": f"Alias '{alias}' associado ao perfil '{p.code}'"}


@router.delete("/{profile_id}/tables/{alias}")
def remove_table_from_profile(
    profile_id: int, alias: str, request: Request,
    db: Session = Depends(get_db), admin=Depends(require_admin),
):
    p = db.query(Profile).filter(Profile.id == profile_id).first()
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Perfil nao encontrado")
    row = db.query(TableProfile).filter(
        TableProfile.profile_id == profile_id, TableProfile.alias == alias.upper()
    ).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Associacao inexistente")
    db.delete(row); db.commit()
    audit.log(db, action="profile.table.remove", user=admin, ip=get_client_ip(request),
              detail=f"profile={p.code} alias={alias.upper()}")
    return {"detail": "Associacao removida"}


# ---- Perfis por usuario --------------------------------------------------

@user_profiles_router.get("/{user_id}/profiles")
def list_user_profiles(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario nao encontrado")
    codes = sorted({up.profile.code for up in user.profile_links if up.profile})
    return {"user_id": user_id, "username": user.username, "profiles": codes}


@user_profiles_router.put("/{user_id}/profiles")
def replace_user_profiles(
    user_id: int, payload: UserProfilesUpdate, request: Request,
    db: Session = Depends(get_db), admin=Depends(require_admin),
):
    """Substitui o conjunto de perfis do usuario por `profile_codes`.
    Aceita lista vazia (remove todos os vinculos).
    """
    user = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario nao encontrado")

    requested = {c.upper().strip() for c in payload.profile_codes if c.strip()}
    profiles_by_code = {p.code: p for p in db.query(Profile).filter(Profile.code.in_(requested)).all()} \
                       if requested else {}
    unknown = requested - profiles_by_code.keys()
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Perfis desconhecidos: {sorted(unknown)}")

    # Apaga vinculos atuais e recria
    db.query(UserProfile).filter(UserProfile.user_id == user_id).delete(synchronize_session=False)
    for code, p in profiles_by_code.items():
        db.add(UserProfile(user_id=user_id, profile_id=p.id))
    db.commit()
    audit.log(db, action="user.profiles.update", user=admin, ip=get_client_ip(request),
              detail=f"target={user.username} profiles={sorted(requested)}")
    return {"detail": "Perfis atualizados", "profiles": sorted(requested)}

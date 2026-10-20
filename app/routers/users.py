"""Список пользователей и текущий пользователь."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUserRequired,AdminUser
from app.database import get_db
from app.security import PasswordError, hash_password
from app.models import User
from app.schemas import UserOut, UserCreateIn

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get("/me", response_model=UserOut)
async def get_me(user: CurrentUserRequired):
    return user


@router.get("", response_model=list[UserOut])
async def list_users(
    user: CurrentUserRequired,
    db: Annotated[AsyncSession, Depends(get_db)],
    role: str | None = Query(default=None),
):
    stmt = select(User).where(User.is_active.is_(True))
    if role:
        stmt = stmt.where(User.role == role)
    rows = (await db.execute(stmt.order_by(User.name))).scalars().all()
    return list(rows)

@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreateIn,
    admin: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "Имя не может быть пустым.")

    exists = (
        await db.execute(select(User).where(User.name == name))
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(409, "Пользователь с таким именем уже существует.")

    try:
        pw_hash = hash_password(payload.password)
    except PasswordError as exc:
        raise HTTPException(400, str(exc)) from exc

    user = User(
        name=name,
        password_hash=pw_hash,
        role=payload.role,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user
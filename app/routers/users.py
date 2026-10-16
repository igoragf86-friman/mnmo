"""Список пользователей и текущий пользователь."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUserRequired
from app.database import get_db
from app.models import User
from app.schemas import UserOut

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
"""JWT-аутентификация: access/refresh, зависимости и роуты."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Request,
    status,
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import RefreshToken, User
from app.security import (
    ACCESS_COOKIE_NAME,
    ACCESS_TOKEN_TTL,
    ACCESS_TOKEN_TYPE,
    REFRESH_COOKIE_NAME,
    REFRESH_TOKEN_TTL,
    REFRESH_TOKEN_TYPE,
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_refresh_token,
    verify_password,
)

templates = Jinja2Templates(directory="./templates")
router = APIRouter()


class AuthRequiredError(Exception):
    """Не авторизован: HTML-страницы → редирект на /login."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_next(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/"
    return value

def _cookie_secure() -> bool:
    return settings.app_env != "development"


def set_auth_cookies(response, access: str, refresh: str) -> None:
    response.set_cookie(
        key=ACCESS_COOKIE_NAME,
        value=access,
        max_age=int(ACCESS_TOKEN_TTL.total_seconds()),
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh,
        max_age=int(REFRESH_TOKEN_TTL.total_seconds()),
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        path="/",
    )


def clear_auth_cookies(response) -> None:
    response.delete_cookie(ACCESS_COOKIE_NAME, path="/")
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/")

async def _issue_tokens(db: AsyncSession, user: User) -> tuple[str, str]:
    access = create_access_token(user.id, user.role)
    refresh = create_refresh_token(user.id)
    db.add(
        RefreshToken(
            token_hash=hash_refresh_token(refresh),
            user_id=user.id,
            expires_at=_now() + REFRESH_TOKEN_TTL,
        )
    )
    await db.commit()
    return access, refresh


async def _load_user_from_refresh(
    db: AsyncSession, refresh_token: str
) -> tuple[User, str] | None:
    """Проверяет refresh в БД и возвращает (user, new_access) или None."""
    try:
        payload = decode_token(refresh_token, REFRESH_TOKEN_TYPE)
        user_id = int(payload["sub"])
    except (TokenError, ValueError):
        return None

    stored = (
        await db.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == hash_refresh_token(refresh_token),
                RefreshToken.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()

    if stored is None or stored.expires_at <= _now():
        return None

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        return None

    return user, create_access_token(user.id, user.role)



async def get_current_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User | None:
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        try:
            payload = decode_token(token, ACCESS_TOKEN_TYPE)
            user = await db.get(User, int(payload["sub"]))
        except (TokenError, ValueError):
            return None
        return user if user and user.is_active else None

    access = request.cookies.get(ACCESS_COOKIE_NAME)
    if access:
        try:
            payload = decode_token(access, ACCESS_TOKEN_TYPE)
            user = await db.get(User, int(payload["sub"]))
            if user and user.is_active:
                return user
        except (TokenError, ValueError):
            pass

    refresh = request.cookies.get(REFRESH_COOKIE_NAME)
    if not refresh:
        return None

    result = await _load_user_from_refresh(db, refresh)
    if result is None:
        return None

    user, new_access = result
    request.state.new_access_token = new_access
    return user


CurrentUser = Annotated[User | None, Depends(get_current_user)]


async def require_user(user: CurrentUser) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется авторизация.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def require_user_page(user: CurrentUser) -> User:
    if user is None:
        raise AuthRequiredError()
    return user


async def require_admin(user: Annotated[User, Depends(require_user)]) -> User:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав.",
        )
    return user


CurrentUserRequired = Annotated[User, Depends(require_user)]
CurrentUserPage = Annotated[User, Depends(require_user_page)]
AdminUser = Annotated[User, Depends(require_admin)]



@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, user: CurrentUser, next: str = "/"):
    target = _safe_next(next)
    if user is not None:
        return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"next": target},
    )


@router.post("/login")
async def login(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next: Annotated[str, Form()] = "/",
):
    username = (username or "").strip()
    target = _safe_next(next)

    def fail(message: str, code: int) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": message, "next": target, "username": username},
            status_code=code,
        )

    if not username or not password:
        return fail("Заполните логин и пароль.", 400)

    user = (
        await db.execute(select(User).where(User.name == username))
    ).scalar_one_or_none()

    if user is None or not verify_password(password, user.password_hash):
        return fail("Неверный логин или пароль.", 401)
    if not user.is_active:
        return fail("Учётная запись отключена.", 403)

    access, refresh = await _issue_tokens(db, user)
    response = RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
    set_auth_cookies(response, access, refresh)
    return response


@router.post("/logout")
async def logout(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    refresh = request.cookies.get(REFRESH_COOKIE_NAME)
    if refresh:
        stored = (
            await db.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == hash_refresh_token(refresh)
                )
            )
        ).scalar_one_or_none()
        if stored is not None and stored.revoked_at is None:
            stored.revoked_at = _now()
            await db.commit()

    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    clear_auth_cookies(response)
    return response



@router.post("/auth/token")
async def api_login(
    db: Annotated[AsyncSession, Depends(get_db)],
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
):
    username = (username or "").strip()
    user = (
        await db.execute(select(User).where(User.name == username))
    ).scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Учётная запись отключена.",
        )

    access, refresh = await _issue_tokens(db, user)
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": int(ACCESS_TOKEN_TTL.total_seconds()),
    }


@router.post("/auth/refresh")
async def api_refresh(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    refresh = request.cookies.get(REFRESH_COOKIE_NAME)
    if not refresh:
        try:
            body = await request.json()
        except Exception:
            body = {}
        refresh = body.get("refresh_token")

    if not refresh:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Не передан refresh-токен.",
        )

    result = await _load_user_from_refresh(db, refresh)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh-токен недействителен.",
        )

    user, _ = result

    old = (
        await db.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == hash_refresh_token(refresh)
            )
        )
    ).scalar_one_or_none()
    if old is not None and old.revoked_at is None:
        old.revoked_at = _now()

    new_access = create_access_token(user.id, user.role)
    new_refresh = create_refresh_token(user.id)
    db.add(
        RefreshToken(
            token_hash=hash_refresh_token(new_refresh),
            user_id=user.id,
            expires_at=_now() + REFRESH_TOKEN_TTL,
        )
    )
    await db.commit()

    response = JSONResponse(
        {
            "access_token": new_access,
            "refresh_token": new_refresh,
            "token_type": "bearer",
            "expires_in": int(ACCESS_TOKEN_TTL.total_seconds()),
        }
    )
    set_auth_cookies(response, new_access, new_refresh)
    return response
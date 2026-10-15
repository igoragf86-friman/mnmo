"""Пароли, JWT и токены."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from jwt import ExpiredSignatureError, InvalidTokenError

from app.config import settings

# --- Cookies / TTL ---
ACCESS_COOKIE_NAME = "access_token"
REFRESH_COOKIE_NAME = "refresh_token"

ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TTL = timedelta(minutes=30)
REFRESH_TOKEN_TTL = timedelta(days=30)

_BCRYPT_MAX_BYTES = 72


class PasswordError(ValueError):
    """Некорректный пароль."""


class TokenError(Exception):
    """Некорректный или просроченный токен."""



def hash_password(password: str) -> str:
    if not isinstance(password, str):
        raise PasswordError("Пароль должен быть строкой.")
    raw = password.encode("utf-8")
    if not raw:
        raise PasswordError("Пароль не может быть пустым.")
    if len(raw) > _BCRYPT_MAX_BYTES:
        raise PasswordError(
            f"Пароль длиннее {_BCRYPT_MAX_BYTES} байт не поддерживается."
        )
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if not isinstance(password, str) or not isinstance(password_hash, str):
        return False
    raw = password.encode("utf-8")
    if not raw or len(raw) > _BCRYPT_MAX_BYTES:
        return False
    try:
        return bcrypt.checkpw(raw, password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------- JWT ----------

def _encode(payload: dict, ttl: timedelta) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        **payload,
        "iat": now,
        "exp": now + ttl,
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(
        claims,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=JWT_ALGORITHM,
    )


def create_access_token(user_id: int, role: str) -> str:
    return _encode(
        {"sub": str(user_id), "role": role, "type": ACCESS_TOKEN_TYPE},
        ACCESS_TOKEN_TTL,
    )


def create_refresh_token(user_id: int) -> str:
    return _encode(
        {"sub": str(user_id), "type": REFRESH_TOKEN_TYPE},
        REFRESH_TOKEN_TTL,
    )


def decode_token(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[JWT_ALGORITHM], 
        )
    except ExpiredSignatureError as exc:
        raise TokenError("Токен истёк.") from exc
    except InvalidTokenError as exc:
        raise TokenError("Некорректный токен.") from exc

    if payload.get("type") != expected_type:
        raise TokenError("Неверный тип токена.")
    if not payload.get("sub"):
        raise TokenError("В токене отсутствует sub.")
    return payload


def hash_refresh_token(token: str) -> str:
    """В БД храним только sha256(refresh)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
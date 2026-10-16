from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import UploadFile

from app.config import settings

ALLOWED_EXTENSIONS: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"
    ),
}

_CHUNK = 1024 * 1024
_SLUG_RE = re.compile(r"[^A-Za-z0-9_.-]+")


class StorageError(Exception):
    """Ошибка при работе с хранилищем."""


def max_file_size_bytes() -> int:
    return settings.documents_max_file_mb * 1024 * 1024


def storage_root() -> Path:
    root = Path(settings.documents_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _ext_of(filename: str) -> str:
    return (filename.rsplit(".", 1)[-1] if "." in filename else "").lower()


def _user_slug(user_id: int, username: str) -> str:
    """Папка пользователя: id-имя. id гарантирует уникальность."""
    s = _SLUG_RE.sub("_", username).strip("._-")[:60] or "user"
    return f"{user_id}-{s}"


def _make_key(user_id: int, username: str, ext: str) -> str:
    now = datetime.now(timezone.utc)
    folder = _user_slug(user_id, username)
    return f"{folder}/{now:%Y/%m}/{secrets.token_hex(16)}.{ext}"


async def save_upload(
    upload: UploadFile, filename: str, user_id: int, username: str
) -> dict:
    ext = _ext_of(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise StorageError(
            f"Формат .{ext or '?'} не поддерживается. "
            f"Разрешены: {', '.join(sorted(ALLOWED_EXTENSIONS))}."
        )

    limit = max_file_size_bytes()
    key = _make_key(user_id, username, ext)
    target = storage_root() / key
    target.parent.mkdir(parents=True, exist_ok=True)

    sha = hashlib.sha256()
    written = 0
    try:
        with target.open("wb") as out:
            while True:
                chunk = await upload.read(_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise StorageError(
                        f"Файл больше {settings.documents_max_file_mb} МБ."
                    )
                sha.update(chunk)
                out.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise

    if written == 0:
        target.unlink(missing_ok=True)
        raise StorageError("Файл пуст.")

    return {
        "storage_path": key,
        "mime_type": ALLOWED_EXTENSIONS[ext],
        "file_size": written,
        "checksum": sha.hexdigest(),
    }


def copy_for_user(
    src_storage_path: str, user_id: int, username: str, ext: str
) -> dict:
    """Копирует файл в папку другого пользователя (для клонирования агента)."""
    src = storage_root() / src_storage_path
    if not src.is_file():
        raise StorageError(f"Исходный файл не найден: {src_storage_path}")

    key = _make_key(user_id, username, ext)
    dst = storage_root() / key
    dst.parent.mkdir(parents=True, exist_ok=True)

    sha = hashlib.sha256()
    written = 0
    with src.open("rb") as fi, dst.open("wb") as fo:
        while True:
            chunk = fi.read(_CHUNK)
            if not chunk:
                break
            written += len(chunk)
            sha.update(chunk)
            fo.write(chunk)

    return {
        "storage_path": key,
        "mime_type": ALLOWED_EXTENSIONS.get(ext, "application/octet-stream"),
        "file_size": written,
        "checksum": sha.hexdigest(),
    }


def delete_file(storage_path: str) -> None:
    try:
        (storage_root() / storage_path).unlink(missing_ok=True)
    except OSError:
        pass
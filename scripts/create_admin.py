#python -m scripts.create_admin
import asyncio
import getpass
import sys

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import User
from app.security import PasswordError, hash_password


async def main() -> int:
    name = input("Логин: ").strip()
    if not name:
        print("Логин не может быть пустым.", file=sys.stderr)
        return 1

    password = getpass.getpass("Пароль: ")
    if password != getpass.getpass("Повторите пароль: "):
        print("Пароли не совпадают.", file=sys.stderr)
        return 1

    try:
        password_hash = hash_password(password)
    except PasswordError as exc:
        print(f"Ошибка пароля: {exc}", file=sys.stderr)
        return 1

    role = (input("Роль (admin/user) [admin]: ").strip() or "admin")
    if role not in {"admin", "user"}:
        print("Недопустимая роль.", file=sys.stderr)
        return 1

    async with AsyncSessionLocal() as db:
        exists = (
            await db.execute(select(User).where(User.name == name))
        ).scalar_one_or_none()
        if exists is not None:
            print("Пользователь с таким логином уже существует.",
                  file=sys.stderr)
            return 1

        user = User(
            name=name,
            password_hash=password_hash,
            role=role,
            is_active=True,
        )
        db.add(user)
        await db.commit()
        print(f"Создан пользователь {name!r} (id={user.id}, role={role}).")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
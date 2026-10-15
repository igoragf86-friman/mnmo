from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    """Базовый класс моделей базы данных."""


engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Создаёт сессию БД для одного запроса FastAPI."""
    async with AsyncSessionLocal() as session:
        yield session


async def check_database_connection() -> None:
    """Проверяет подключение к PostgreSQL."""
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
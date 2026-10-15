from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.config import settings
from app.database import check_database_connection, engine
from app.qwen import QwenError, qwen_client

import logging

logger = logging.getLogger(__name__)
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

    # Закрываем пул подключений PostgreSQL при остановке приложения.
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
    )


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    system_prompt: str | None = Field(default=None, max_length=10000)


class ChatResponse(BaseModel):
    answer: str


@app.get("/api/v1/health/database")
async def database_health():
    """Проверяет подключение к PostgreSQL."""
    try:
        await check_database_connection()
        return {
            "database": "postgresql",
            "status": "connected",
        }
    except Exception:
        logger.exception("Ошибка подключения к PostgreSQL")
        raise HTTPException(
            status_code=503,
            detail="Не удалось подключиться к PostgreSQL",
        ) from None


@app.get("/api/v1/health/database")
async def database_health():
    """Проверяет подключение к PostgreSQL."""
    try:
        await check_database_connection()
        return {
            "database": "postgresql",
            "status": "connected",
        }
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Не удалось подключиться к PostgreSQL",
        ) from None


@app.post("/api/v1/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Отправляет сообщение в Qwen."""
    try:
        answer = await qwen_client.chat(
            message=request.message,
            system_prompt=request.system_prompt,
        )

        return ChatResponse(answer=answer)

    except QwenError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc
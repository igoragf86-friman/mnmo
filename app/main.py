from contextlib import asynccontextmanager
from urllib.parse import quote
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from app.auth import AuthRequiredError, set_auth_cookies, require_user_page
from app.auth import router as auth_router
from app.config import settings
from app.database import check_database_connection, engine
from app.qwen import QwenError, qwen_client
from app.rag import close_embedding_client

from app.routers.chat import router as chat_router
from app.routers.documents import router as documents_router
from app.routers.users import router as users_router
from app.routers.agents import router as agents_router

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
app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(users_router)
app.include_router(agents_router)
app.include_router(chat_router)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

@app.on_event("shutdown")
async def _shutdown():
    await qwen_client.close()
    await close_embedding_client()


@app.middleware("http")
async def persist_refreshed_access(request: Request, call_next):
    response = await call_next(request)
    new_access = getattr(request.state, "new_access_token", None)
    if new_access is not None:
        from app.security import (
            ACCESS_COOKIE_NAME,
            ACCESS_TOKEN_TTL,
        )
        from app.config import settings

        response.set_cookie(
            key=ACCESS_COOKIE_NAME,
            value=new_access,
            max_age=int(ACCESS_TOKEN_TTL.total_seconds()),
            httponly=True,
            secure=settings.app_env != "development",
            samesite="lax",
            path="/",
        )
    return response


@app.exception_handler(AuthRequiredError)
async def _redirect_to_login(request: Request, exc: AuthRequiredError):
    next_url = request.url.path
    if request.url.query:
        next_url = f"{next_url}?{request.url.query}"
    return RedirectResponse(
        f"/login?next={quote(next_url, safe='/')}",
        status_code=303,
    )
@app.get("/")
async def index(request: Request, user: User = Depends(require_user_page)):
    return templates.TemplateResponse(
        request=request, name="index.html", context={"user": user}
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


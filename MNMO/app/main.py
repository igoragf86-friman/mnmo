
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.qwen import QwenError, qwen_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    system_prompt: str | None = Field(
        default=None,
        max_length=10000,
    )


class ChatResponse(BaseModel):
    answer: str


@app.get("/")
async def root():
    return {
        "name": settings.app_name,
        "status": "running",
    }


@app.get("/api/v1/health")
async def health():
    result = await qwen_client.health()

    if not result["healthy"]:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "llama.cpp server недоступен "
                           "или ещё не готов",
                **result,
            },
        )

    return result


@app.post(
    "/api/v1/chat",
    response_model=ChatResponse,
)
async def chat(request: ChatRequest):
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

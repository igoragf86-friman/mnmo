"""Чат: RAG + Qwen, с потоковой передачей токенов."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUserRequired
from app.database import get_db
from app.qwen import QwenError, qwen_client
from app.rag import search_chunks
from app.schemas import ChatIn, ChatOut

router = APIRouter(prefix="/api/v1", tags=["chat"])


def _build_context(hits) -> str:
    if not hits:
        return ""
    return "\n\n---\n\n".join(
        f"[{i + 1}] {chunk.content}" for i, (chunk, _) in enumerate(hits)
    )


@router.post("/chat", response_model=ChatOut)
async def chat(
    payload: ChatIn,
    user: CurrentUserRequired,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    context = ""
    if payload.agent_id is not None:
        hits = await search_chunks(db, payload.agent_id, payload.message)
        context = _build_context(hits)

    try:
        answer = await qwen_client.chat(
            message=payload.message,
            system_prompt=payload.system_prompt,
            context=context,
        )
    except QwenError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return ChatOut(answer=answer)


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatIn,
    user: CurrentUserRequired,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    context = ""
    if payload.agent_id is not None:
        hits = await search_chunks(db, payload.agent_id, payload.message)
        context = _build_context(hits)

    async def gen():
        try:
            async for token in qwen_client.chat_stream(
                message=payload.message,
                system_prompt=payload.system_prompt,
                context=context,
            ):
                yield (
                    "data: "
                    + json.dumps({"delta": token}, ensure_ascii=False)
                    + "\n\n"
                )
        except QwenError as exc:
            yield (
                "data: "
                + json.dumps({"error": str(exc)}, ensure_ascii=False)
                + "\n\n"
            )
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
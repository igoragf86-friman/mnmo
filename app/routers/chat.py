from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUserRequired
from app.database import AsyncSessionLocal, get_db
from app.models import Conversation, Message
from app.qwen import QwenError, qwen_client
from app.rag import search_chunks
from app.schemas import ChatIn, ChatOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["chat"])

HISTORY_LIMIT = 20
HISTORY_MAX_CHARS = 12000


async def _get_or_create_conv(
    db: AsyncSession,
    user_id: int,
    agent_id: int,
    conv_id: int | None,
) -> Conversation:
    if conv_id is not None:
        conv = await db.get(Conversation, conv_id)
        if conv is None or conv.user_id != user_id:
            raise HTTPException(404, "Переписка не найдена.")
        if conv.agent_id != agent_id:
            raise HTTPException(400, "Переписка принадлежит другому агенту.")
        return conv

    conv = Conversation(user_id=user_id, agent_id=agent_id)
    db.add(conv)
    await db.flush()
    return conv


async def _load_history(
    db: AsyncSession, conv_id: int
) -> list[dict]:
    rows = (
        await db.execute(
            select(Message)
            .where(
                Message.conversation_id == conv_id,
                Message.role.in_(["user", "assistant"]),
            )
            .order_by(Message.created_at.desc())
            .limit(HISTORY_LIMIT)
        )
    ).scalars().all()

    messages = list(reversed(rows))
    total = 0
    trimmed: list[dict] = []
    for m in messages:
        total += len(m.content)
        if total > HISTORY_MAX_CHARS:
            break
        trimmed.append({"role": m.role, "content": m.content})
    return list(reversed(trimmed))  


async def _save_user_message(
    db: AsyncSession, conv: Conversation, text: str
) -> None:
    db.add(
        Message(
            conversation_id=conv.id,
            role="user",
            content=text,
            metadata_={"agent_id": conv.agent_id},
        )
    )
    await db.commit()


async def _save_assistant_message(
    conv_id: int, text: str, meta: dict | None = None
) -> None:
    async with AsyncSessionLocal() as save_db:
        save_db.add(
            Message(
                conversation_id=conv_id,
                role="assistant",
                content=text,
                metadata_=meta or {},
            )
        )
        conv = await save_db.get(Conversation, conv_id)
        if conv is not None:
            from sqlalchemy import func
            conv.title = conv.title or None
            await save_db.flush()
        await save_db.commit()


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
    if payload.agent_id is None:
        raise HTTPException(400, "agent_id обязателен.")

    conv = await _get_or_create_conv(
        db, user.id, payload.agent_id, payload.conversation_id
    )
    history = await _load_history(db, conv.id)
    await _save_user_message(db, conv, payload.message)

    context = ""
    hits = await search_chunks(db, payload.agent_id, payload.message)
    context = _build_context(hits)

    try:
        answer = await qwen_client.chat(
            message=payload.message,
            system_prompt=payload.system_prompt,
            context=context,
            history=history,
        )
    except QwenError as exc:
        raise HTTPException(502, str(exc)) from exc

    await _save_assistant_message(conv.id, answer)
    return ChatOut(answer=answer, conversation_id=conv.id)


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatIn,
    user: CurrentUserRequired,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if payload.agent_id is None:
        raise HTTPException(400, "agent_id обязателен.")

    conv = await _get_or_create_conv(
        db, user.id, payload.agent_id, payload.conversation_id
    )
    history = await _load_history(db, conv.id)
    await _save_user_message(db, conv, payload.message)

    hits = await search_chunks(db, payload.agent_id, payload.message)
    context = _build_context(hits)
    conv_id = conv.id

    async def gen():
        answer_parts: list[str] = []
        error_text = None
        try:
            async for token in qwen_client.chat_stream(
                message=payload.message,
                system_prompt=payload.system_prompt,
                context=context,
                history=history,
            ):
                answer_parts.append(token)
                yield (
                    "data: "
                    + json.dumps({"delta": token}, ensure_ascii=False)
                    + "\n\n"
                )
        except QwenError as exc:
            error_text = str(exc)
            yield (
                "data: "
                + json.dumps({"error": error_text}, ensure_ascii=False)
                + "\n\n"
            )
        finally:
            answer = "".join(answer_parts).strip()
            if answer:
                meta = {"error": error_text} if error_text else {}
                try:
                    await _save_assistant_message(conv_id, answer, meta)
                except Exception:
                    logger.exception("Не удалось сохранить ответ ассистента")
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
            "X-Conversation-Id": str(conv_id),
        },
    )
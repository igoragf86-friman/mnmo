import asyncio
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
from app.state import active_generations
from app.config import settings
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["chat"])

HISTORY_LIMIT = 20
HISTORY_MAX_CHARS = 12000

# Глобальное хранилище активных генераций
# { conv_id: {"text": str, "active": bool, "subscribers": [asyncio.Queue]} }


async def _get_or_create_conv(db: AsyncSession, user_id: int, agent_id: int, conv_id: int | None) -> Conversation:
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


async def _load_history(db: AsyncSession, conv_id: int) -> list[dict]:
    rows = (await db.execute(
        select(Message)
        .where(Message.conversation_id == conv_id, Message.role.in_(["user", "assistant"]))
        .order_by(Message.created_at.desc())
        .limit(HISTORY_LIMIT)
    )).scalars().all()

    messages = list(reversed(rows))
    total = 0
    trimmed: list[dict] = []
    for m in messages:
        total += len(m.content)
        if total > HISTORY_MAX_CHARS:
            break
        trimmed.append({"role": m.role, "content": m.content})
    return list(reversed(trimmed))


async def _save_user_message(db: AsyncSession, conv: Conversation, text: str) -> None:
    db.add(Message(conversation_id=conv.id, role="user", content=text, metadata_={"agent_id": conv.agent_id}))
    await db.commit()


async def _save_assistant_message(conv_id: int, text: str, meta: dict | None = None) -> None:
    async with AsyncSessionLocal() as save_db:
        save_db.add(Message(conversation_id=conv_id, role="assistant", content=text, metadata_=meta or {}))
        await save_db.commit()


def _build_context(hits) -> str:
    if not hits:
        return ""
    return "\n\n---\n\n".join(f"[{i + 1}] {chunk.content}" for i, (chunk, _) in enumerate(hits))

async def _run_generation(conv_id: int, payload: ChatIn, context: str, history: list, state: dict):
    state["task"] = asyncio.current_task()
    try:
        async with asyncio.timeout(settings.qwen_timeout + 30):
            async for chunk in qwen_client.chat_stream(
                message=payload.message,
                system_prompt=payload.system_prompt,
                context=context,
                history=history,
            ):
                if isinstance(chunk, dict):
                    content = chunk.get("content", "")
                else:
                    content = str(chunk)

                state["text"] += content
                
                if content:
                    for q in list(state["subscribers"]):
                        await q.put(content)

    except asyncio.TimeoutError:
        err = "\n\n*[Ошибка: Превышено время ожидания ответа от нейросети]*"
        state["text"] += err
        for q in list(state["subscribers"]):
            await q.put(err)
    except asyncio.CancelledError:
        state["text"] += "\n\n*[Генерация остановлена]*"
        for q in list(state["subscribers"]):
            await q.put("\n\n*[Генерация остановлена]*")
        raise
    except Exception as exc:
        err = f"\n\n*[Ошибка: {exc}]*"
        state["text"] += err
        for q in list(state["subscribers"]):
            await q.put(err)
    finally:
        state["active"] = False
        for q in list(state["subscribers"]):
            await q.put(None)
        
        if state["text"].strip():
            await _save_assistant_message(conv_id, state["text"])
        
        async def cleanup():
            await asyncio.sleep(120)
            if active_generations.get(conv_id) == state:
                active_generations.pop(conv_id, None)
        asyncio.create_task(cleanup())


@router.post("/chat/stream")
async def chat_stream(payload: ChatIn, user: CurrentUserRequired, db: Annotated[AsyncSession, Depends(get_db)]):
    if payload.agent_id is None:
        raise HTTPException(400, "agent_id обязателен.")

    conv = await _get_or_create_conv(db, user.id, payload.agent_id, payload.conversation_id)
    conv_id = conv.id

    state = active_generations.get(conv_id)
    is_active = False
    if state:
        task = state.get("task")
        if state.get("active") and task and not task.done():
            is_active = True

    if not is_active:
        history = await _load_history(db, conv_id)
        await _save_user_message(db, conv, payload.message)
        hits = await search_chunks(db, payload.agent_id, payload.message)
        context = _build_context(hits)
        
        new_state = {"text": "", "active": True, "subscribers": []}
        active_generations[conv_id] = new_state
        asyncio.create_task(_run_generation(conv_id, payload, context, history, new_state))
        state = new_state

    q = asyncio.Queue()
    state["subscribers"].append(q)

    async def gen():
        try:
            if state["text"]:
                yield f"data: {json.dumps({'reset': True, 'text': state['text']}, ensure_ascii=False)}\n\n"
            
            while True:
                token = await q.get()
                if token is None:
                    break
                yield f"data: {json.dumps({'delta': token}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            if q in state["subscribers"]:
                state["subscribers"].remove(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"X-Conversation-Id": str(conv_id)})

@router.post("/chat", response_model=ChatOut)
async def chat(payload: ChatIn, user: CurrentUserRequired, db: Annotated[AsyncSession, Depends(get_db)]):
    if payload.agent_id is None:
        raise HTTPException(400, "agent_id обязателен.")

    conv = await _get_or_create_conv(db, user.id, payload.agent_id, payload.conversation_id)
    history = await _load_history(db, conv.id)
    await _save_user_message(db, conv, payload.message)

    hits = await search_chunks(db, payload.agent_id, payload.message)
    context = _build_context(hits)

    try:
        answer = await qwen_client.chat(message=payload.message, system_prompt=payload.system_prompt, context=context, history=history)
    except QwenError as exc:
        raise HTTPException(502, str(exc)) from exc

    await _save_assistant_message(conv.id, answer)
    return ChatOut(answer=answer, conversation_id=conv.id)



@router.get("/chat/stream/{conv_id}")
async def reconnect_stream(conv_id: int, user: CurrentUserRequired):
    """Позволяет переподключиться к идущей генерации после обновления страницы."""
    if conv_id not in active_generations or not active_generations[conv_id]["active"]:
        raise HTTPException(404, "Нет активной генерации")
    
    state = active_generations[conv_id]
    q = asyncio.Queue()
    state["subscribers"].append(q)

    async def gen():
        try:
            yield f"data: {json.dumps({'reset': True, 'text': state['text']}, ensure_ascii=False)}\n\n"
            
            while True:
                token = await q.get()
                if token is None:
                    break
                yield f"data: {json.dumps({'delta': token}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            if q in state["subscribers"]:
                state["subscribers"].remove(q)

    return StreamingResponse(gen(), media_type="text/event-stream")

@router.delete("/chat/stream/{conv_id}")
async def stop_stream(conv_id: int, user: CurrentUserRequired):
    """Принудительно останавливает генерацию и ждет её завершения."""
    if conv_id in active_generations:
        task = active_generations[conv_id].get("task")
        if task and not task.done():
            task.cancel()
            try:
                await task 
            except asyncio.CancelledError:
                pass
        
        state = active_generations.get(conv_id)
        if state:
            state["active"] = False
            for q in list(state["subscribers"]):
                await q.put(None)
            active_generations.pop(conv_id, None)

    return {"ok": True}
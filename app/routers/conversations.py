from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import CurrentUserRequired
from app.database import get_db
from app.models import Agent, Conversation, Message
from app.schemas import (
    ConversationDetailOut,
    ConversationOut,
    MessageOut,
)

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    user: CurrentUserRequired,
    db: Annotated[AsyncSession, Depends(get_db)],
    agent_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    stmt = (
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .options(selectinload(Conversation.agent))
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
    )
    if agent_id is not None:
        stmt = stmt.where(Conversation.agent_id == agent_id)

    rows = (await db.execute(stmt)).scalars().all()
    result: list[ConversationOut] = []

    for conv in rows:
        cnt = (
            await db.execute(
                select(func.count(Message.id)).where(
                    Message.conversation_id == conv.id
                )
            )
        ).scalar_one()

        last = (
            await db.execute(
                select(Message)
                .where(Message.conversation_id == conv.id)
                .order_by(Message.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        result.append(
            ConversationOut(
                id=conv.id,
                agent_id=conv.agent_id,
                agent_name=conv.agent.name if conv.agent else None,
                title=conv.title,
                created_at=conv.created_at,
                updated_at=conv.updated_at,
                message_count=cnt,
                last_message=last.content[:200] if last else None,
            )
        )
    return result


@router.get("/{conv_id}", response_model=ConversationDetailOut)
async def get_conversation(
    conv_id: int,
    user: CurrentUserRequired,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    conv = (
        await db.execute(
            select(Conversation)
            .where(
                Conversation.id == conv_id,
                Conversation.user_id == user.id,
            )
            .options(
                selectinload(Conversation.agent),
                selectinload(Conversation.messages),
            )
        )
    ).scalar_one_or_none()
    if conv is None:
        raise HTTPException(404, "Переписка не найдена.")

    return ConversationDetailOut(
        id=conv.id,
        agent_id=conv.agent_id,
        agent_name=conv.agent.name if conv.agent else None,
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        message_count=len(conv.messages),
        last_message=conv.messages[-1].content[:200] if conv.messages else None,
        messages=[
            MessageOut(
                id=m.id,
                role=m.role,
                content=m.content,
                created_at=m.created_at,
            )
            for m in conv.messages
        ],
    )


@router.delete(
    "/{conv_id}/messages", status_code=status.HTTP_204_NO_CONTENT
)
async def clear_messages(
    conv_id: int,
    user: CurrentUserRequired,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    conv = await db.get(Conversation, conv_id)
    if conv is None or conv.user_id != user.id:
        raise HTTPException(404, "Переписка не найдена.")

    await db.execute(
        delete(Message).where(Message.conversation_id == conv_id)
    )
    await db.commit()
    return None


@router.delete("/{conv_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conv_id: int,
    user: CurrentUserRequired,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    conv = await db.get(Conversation, conv_id)
    if conv is None or conv.user_id != user.id:
        raise HTTPException(404, "Переписка не найдена.")
    await db.delete(conv)
    await db.commit()
    return None
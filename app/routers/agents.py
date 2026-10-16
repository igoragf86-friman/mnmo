from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import CurrentUserRequired
from app.database import get_db
from app.models import (
    Agent,
    AgentDocument,
    AgentShare,
    Document,
    User,
)
from app.schemas import (
    AgentIn,
    AgentListOut,
    AgentOut,
    AgentShareIn,
    AgentShareOut,
    AgentDocOut,
)
from app.storage import StorageError, copy_for_user

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])



def _can_edit(user: User, agent: Agent) -> bool:
    return user.role == "admin" or agent.created_by == user.id


def _is_owner(user: User, agent: Agent) -> bool:
    return agent.created_by == user.id


def _visible_agents_clause(user: User):
    if user.role == "admin":
        return None                   
    return or_(
        Agent.created_by == user.id,
        Agent.id.in_(
            select(AgentShare.agent_id).where(AgentShare.user_id == user.id)
        ),
    )



async def _doc_ids_for(db: AsyncSession, agent_id: int) -> list[int]:
    rows = (
        await db.execute(
            select(AgentDocument.document_id).where(
                AgentDocument.agent_id == agent_id
            )
        )
    ).scalars().all()
    return list(rows)


async def _docs_for(
    db: AsyncSession, agent_id: int, viewer_id: int
) -> list[AgentDocOut]:
    rows = (
        await db.execute(
            select(AgentDocument, Document, User)
            .join(Document, Document.id == AgentDocument.document_id)
            .join(User, User.id == Document.uploaded_by)
            .where(AgentDocument.agent_id == agent_id)
            .order_by(Document.created_at.desc())
        )
    ).all()
    return [
        AgentDocOut(
            id=doc.id,
            filename=doc.filename,
            file_size=doc.file_size,
            mime_type=doc.mime_type,
            is_enabled=link.is_enabled,
            is_inherited=link.is_inherited,
            owner_id=owner.id,
            owner_name=owner.name,
        )
        for link, doc, owner in rows
    ]


async def _to_out(db: AsyncSession, user: User, agent: Agent) -> AgentOut:
    docs = await _docs_for(db, agent.id, user.id)
    is_owner = _is_owner(user, agent)
    is_admin = user.role == "admin"
    can_edit = is_owner or is_admin

    shared_with_me = False
    if not can_edit:
        shared_with_me = (
            await db.execute(
                select(AgentShare).where(
                    AgentShare.agent_id == agent.id,
                    AgentShare.user_id == user.id,
                )
            )
        ).scalar_one_or_none() is not None

    share_ids: list[int] = []
    if can_edit:
        share_ids = list(
            (await db.execute(
                select(AgentShare.user_id).where(AgentShare.agent_id == agent.id)
            )).scalars().all()
        )

    return AgentOut(
        id=agent.id,
        name=agent.name,
        description=agent.description,
        system_prompt=agent.system_prompt,
        model_name=agent.model_name,
        icon=agent.icon,
        color=agent.color,
        is_active=agent.is_active,
        is_public=agent.is_public,
        created_by=agent.created_by,
        created_at=agent.created_at,
        owner_name=agent.created_by_user.name if agent.created_by_user else None,
        document_ids=[d.id for d in docs],
        enabled_document_ids=[d.id for d in docs if d.is_enabled],
        documents=docs,
        document_count=len([d for d in docs if d.is_enabled]),
        is_owner=is_owner,
        can_edit=can_edit,
        can_share=can_edit,
        can_share_all=can_edit,
        is_shared_with_me=shared_with_me,
        share_user_ids=share_ids,
    )



@router.get("", response_model=AgentListOut)
async def list_agents(
    user: CurrentUserRequired,
    db: Annotated[AsyncSession, Depends(get_db)],
    mine: bool = False,
):
    base = select(Agent).options(
        selectinload(Agent.created_by_user)
    )
    vis = _visible_agents_clause(user)
    if vis is not None:
        base = base.where(vis)
    if mine:
        base = base.where(Agent.created_by == user.id)

    rows = (
        await db.execute(base.order_by(Agent.created_at.desc()))
    ).scalars().all()
    items = [await _to_out(db, user, a) for a in rows]
    return AgentListOut(items=items, total=len(items))

@router.get("/catalog", response_model=AgentListOut)
async def catalog(
    user: CurrentUserRequired,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    rows = (
        await db.execute(
            select(Agent)
            .where(Agent.is_public.is_(True), Agent.is_active.is_(True))
            .options(selectinload(Agent.created_by_user))
            .order_by(Agent.created_at.desc())
        )
    ).scalars().all()
    items = [await _to_out(db, user, a) for a in rows]
    return AgentListOut(items=items, total=len(items))

@router.get("/{agent_id}/shares", response_model=list[AgentShareOut])
async def list_shares(
    agent_id: int,
    user: CurrentUserRequired,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "Агент не найден.")
    if not _can_edit(user, agent):
        raise HTTPException(403, "Нет прав.")

    rows = (
        await db.execute(
            select(AgentShare, User)
            .join(User, User.id == AgentShare.user_id)
            .where(AgentShare.agent_id == agent_id)
            .order_by(User.name)
        )
    ).all()
    return [
        AgentShareOut(user_id=u.id, name=u.name, role=u.role)
        for _, u in rows
    ]

@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(
    agent_id: int,
    user: CurrentUserRequired,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    agent = (
        await db.execute(
            select(Agent)
            .where(Agent.id == agent_id)
            .options(selectinload(Agent.created_by_user))
        )
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(404, "Агент не найден.")

    if not agent.is_public and user.role != "admin":
        vis = _visible_agents_clause(user)
        allowed = (
            await db.execute(select(Agent).where(Agent.id == agent_id, vis))
        ).scalar_one_or_none()
        if allowed is None:
            raise HTTPException(403, "Нет доступа к агенту.")

    return await _to_out(db, user, agent)



async def _resolve_docs(
    db: AsyncSession, user: User, doc_ids: list[int]
) -> list[Document]:
    if not doc_ids:
        return []
    uniq = list(dict.fromkeys(doc_ids))
    rows = (
        await db.execute(select(Document).where(Document.id.in_(uniq)))
    ).scalars().all()
    by_id = {d.id: d for d in rows}

    missing = [i for i in uniq if i not in by_id]
    if missing:
        raise HTTPException(
            400, f"Документы не найдены: {missing}"
        )
    if user.role != "admin":
        not_mine = [d.id for d in rows if d.uploaded_by != user.id]
        if not_mine:
            raise HTTPException(
                403,
                f"Нельзя прикреплять чужие документы: {not_mine}",
            )
    return list(by_id.values())


async def _set_docs(
    db: AsyncSession, agent: Agent, user: User, enabled_ids: list[int]
) -> None:
    """`enabled_ids` — какие документы должны быть включены у агента.

    Уже привязанные документы (в т.ч. чужие) не отвязываются, а
    переключаются через `is_enabled`. Новые — привязываются, если
    документ принадлежит пользователю, либо пользователь админ.
    """
    requested = set(enabled_ids or [])

    current = {
        link.document_id: link
        for link in (
            await db.execute(
                select(AgentDocument).where(AgentDocument.agent_id == agent.id)
            )
        ).scalars().all()
    }

    # Проверка прав — только для не-админов.
    if user.role != "admin":
        allowed = set(current.keys())
        own = set(
            (await db.execute(
                select(Document.id).where(Document.uploaded_by == user.id)
            )).scalars().all()
        )
        allowed |= own

        forbidden = requested - allowed
        if forbidden:
            raise HTTPException(
                403,
                f"Нельзя прикреплять чужие документы: {sorted(forbidden)}",
            )

    for doc_id in requested:
        if doc_id in current:
            current[doc_id].is_enabled = True
        else:
            db.add(
                AgentDocument(
                    agent_id=agent.id,
                    document_id=doc_id,
                    attached_by=user.id,
                    is_enabled=True,
                )
            )

    for doc_id, link in current.items():
        if doc_id not in requested:
            link.is_enabled = False



@router.post(
    "", response_model=AgentOut, status_code=status.HTTP_201_CREATED
)
async def create_agent(
    payload: AgentIn,
    user: CurrentUserRequired,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    agent = Agent(
        name=payload.name.strip(),
        description=(payload.description or None),
        system_prompt=payload.system_prompt or "",
        model_name=payload.model_name or "qwen",
        icon=payload.icon or "bot",
        color=payload.color or "#4f46e5",
        created_by=user.id,
    )
    db.add(agent)
    await db.flush()
    await _set_docs(db, agent, user, payload.document_ids)
    await db.commit()

    agent = (
        await db.execute(
            select(Agent)
            .where(Agent.id == agent.id)
            .options(selectinload(Agent.created_by_user))
        )
    ).scalar_one()
    return await _to_out(db, user, agent)


@router.put("/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: int,
    payload: AgentIn,
    user: CurrentUserRequired,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "Агент не найден.")
    if not _can_edit(user, agent):
        raise HTTPException(403, "Нет прав на редактирование.")

    agent.name = payload.name.strip()
    agent.description = payload.description or None
    agent.system_prompt = payload.system_prompt or ""
    agent.model_name = payload.model_name or "qwen"
    agent.icon = payload.icon or "bot"
    agent.color = payload.color or "#4f46e5"
    await _set_docs(db, agent, user, payload.document_ids)
    await db.commit()

    agent = (
        await db.execute(
            select(Agent)
            .where(Agent.id == agent.id)
            .options(selectinload(Agent.created_by_user))
        )
    ).scalar_one()
    return await _to_out(db, user, agent)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: int,
    user: CurrentUserRequired,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "Агент не найден.")
    if not _can_edit(user, agent):
        raise HTTPException(403, "Нет прав на удаление.")
    await db.delete(agent)
    await db.commit()
    return None



@router.post(
    "/{agent_id}/clone",
    response_model=AgentOut,
    status_code=status.HTTP_201_CREATED,
)
async def clone_agent(
    agent_id: int,
    user: CurrentUserRequired,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    src = (
        await db.execute(
            select(Agent)
            .where(Agent.id == agent_id)
            .options(
                selectinload(Agent.created_by_user),
                selectinload(Agent.document_links),
            )
        )
    ).scalar_one_or_none()
    if src is None:
        raise HTTPException(404, "Агент не найден.")

    is_owner = src.created_by == user.id
    is_admin = user.role == "admin"
    is_shared = (
        await db.execute(
            select(AgentShare).where(
                AgentShare.agent_id == src.id,
                AgentShare.user_id == user.id,
            )
        )
    ).scalar_one_or_none() is not None
    if not (is_owner or is_admin or is_shared or src.is_public):
        raise HTTPException(403, "Нет доступа к агенту.")

    new_agent = Agent(
        name=f"{src.name} (копия)",
        description=src.description,
        system_prompt=src.system_prompt,
        model_name=src.model_name,
        icon=src.icon,
        color=src.color,
        created_by=user.id,
    )
    db.add(new_agent)
    await db.flush()

    for link in src.document_links:
        db.add(
            AgentDocument(
                agent_id=new_agent.id,
                document_id=link.document_id,
                attached_by=user.id,
                is_enabled=link.is_enabled,
                is_inherited=True,
            )
        )

    await db.commit()

    new_agent = (
        await db.execute(
            select(Agent)
            .where(Agent.id == new_agent.id)
            .options(selectinload(Agent.created_by_user))
        )
    ).scalar_one()
    return await _to_out(db, user, new_agent)

@router.post(
    "/{agent_id}/share", status_code=status.HTTP_204_NO_CONTENT
)
async def share_agent(
    agent_id: int,
    payload: AgentShareIn,
    user: CurrentUserRequired,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "Агент не найден.")
    if not _can_edit(user, agent):
        raise HTTPException(403, "Нет прав на шаринг.")

    target = await db.get(User, payload.user_id)
    if target is None or not target.is_active:
        raise HTTPException(404, "Пользователь не найден.")
    if target.id == agent.created_by:
        raise HTTPException(400, "Владелец уже имеет доступ.")

    exists = (
        await db.execute(
            select(AgentShare).where(
                AgentShare.agent_id == agent_id,
                AgentShare.user_id == target.id,
            )
        )
    ).scalar_one_or_none()
    if exists is not None:
        return None

    db.add(
        AgentShare(
            agent_id=agent_id,
            user_id=target.id,
            shared_by=user.id,
        )
    )
    await db.commit()
    return None


@router.delete(
    "/{agent_id}/share/{target_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unshare_agent(
    agent_id: int,
    target_user_id: int,
    user: CurrentUserRequired,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "Агент не найден.")
    if not _can_edit(user, agent):
        raise HTTPException(403, "Нет прав.")

    row = (
        await db.execute(
            select(AgentShare).where(
                AgentShare.agent_id == agent_id,
                AgentShare.user_id == target_user_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    await db.delete(row)
    await db.commit()
    return None
@router.post("/{agent_id}/publish", response_model=AgentOut)
async def publish_agent(
    agent_id: int,
    user: CurrentUserRequired,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "Агент не найден.")
    if not _can_edit(user, agent):
        raise HTTPException(403, "Нет прав.")
    agent.is_public = True
    await db.commit()
    agent = (
        await db.execute(
            select(Agent).where(Agent.id == agent_id)
            .options(selectinload(Agent.created_by_user))
        )
    ).scalar_one()
    return await _to_out(db, user, agent)


@router.delete("/{agent_id}/publish", status_code=status.HTTP_204_NO_CONTENT)
async def unpublish_agent(
    agent_id: int,
    user: CurrentUserRequired,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "Агент не найден.")
    if not _can_edit(user, agent):
        raise HTTPException(403, "Нет прав.")
    agent.is_public = False
    await db.commit()
    return None

@router.delete(
    "/{agent_id}/shared-with-me",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unshare_from_me(
    agent_id: int,
    user: CurrentUserRequired,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Получатель убирает агента из списка «поделились со мной»."""
    row = (
        await db.execute(
            select(AgentShare).where(
                AgentShare.agent_id == agent_id,
                AgentShare.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        await db.delete(row)
        await db.commit()
    return None
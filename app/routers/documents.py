from __future__ import annotations

from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import CurrentUserRequired
from app.database import get_db
from app.models import Document, User
from app.schemas import DocumentListOut, DocumentOut
from app.storage import StorageError, delete_file, save_upload

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


def _can_delete(user: User, doc: Document) -> bool:
    return user.role == "admin" or doc.uploaded_by == user.id


def _to_out(user: User, doc: Document) -> DocumentOut:
    return DocumentOut(
        id=doc.id,
        filename=doc.filename,
        mime_type=doc.mime_type,
        file_size=doc.file_size,
        status=doc.status,
        created_at=doc.created_at,
        uploaded_by=doc.uploaded_by_user,
        can_delete=_can_delete(user, doc),
    )


def _visible_clause(user: User):
    """Пользователь видит только свои; админ — все."""
    if user.role == "admin":
        return None
    return Document.uploaded_by == user.id


@router.post(
    "",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    user: CurrentUserRequired,
    db: Annotated[AsyncSession, Depends(get_db)],
    file: Annotated[UploadFile, File(...)],
):
    filename = (file.filename or "file").strip() or "file"

    try:
        meta = await save_upload(file, filename, user.id, user.name)
    except StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    doc = Document(
        uploaded_by=user.id,
        filename=filename,
        storage_path=meta["storage_path"],
        mime_type=meta["mime_type"],
        file_size=meta["file_size"],
        checksum=meta["checksum"],
        status="uploaded",
    )
    db.add(doc)
    await db.commit()

    doc = (
        await db.execute(
            select(Document)
            .where(Document.id == doc.id)
            .options(selectinload(Document.uploaded_by_user))
        )
    ).scalar_one()

    return _to_out(user, doc)


@router.get("", response_model=DocumentListOut)
async def list_documents(
    user: CurrentUserRequired,
    db: Annotated[AsyncSession, Depends(get_db)],
    ext: Literal["pdf", "docx"] | None = None,
    q: str | None = None,
    mine: bool = False,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    base = select(Document).options(selectinload(Document.uploaded_by_user))

    vis = _visible_clause(user)
    if vis is not None:
        base = base.where(vis)

    if mine:
        base = base.where(Document.uploaded_by == user.id)

    if ext:
        base = base.where(
            func.lower(Document.filename).like(f"%.{ext.lower()}")
        )

    if q:
        base = base.where(
            func.lower(Document.filename).like(f"%{q.strip().lower()}%")
        )

    total = (
        await db.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()

    rows = (
        await db.execute(
            base.order_by(Document.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    return DocumentListOut(
        items=[_to_out(user, d) for d in rows],
        total=total,
    )


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    doc_id: int,
    user: CurrentUserRequired,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    doc = await db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(404, "Документ не найден.")
    if not _can_delete(user, doc):
        raise HTTPException(403, "Нет прав на удаление.")

    path = doc.storage_path
    await db.delete(doc)
    await db.commit()
    delete_file(path)
    return None
 
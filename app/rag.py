from __future__ import annotations
import re
import logging
from pathlib import Path
import asyncio
import httpx
import math
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.state import publish_document_event
from app.config import settings
from app.models import AgentDocument, Document, DocumentChunk

logger = logging.getLogger(__name__)

_embed_client: httpx.AsyncClient | None = None

def extract_text(path: Path, ext: str) -> str:
    ext = ext.lower()
    
    if ext == "pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        parts = []
        for page in reader.pages:
            try: parts.append(page.extract_text() or "")
            except Exception: pass
        return "\n\n".join(p for p in parts if p.strip())

    if ext == "docx":
        import docx
        doc = docx.Document(str(path))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells]
                if any(cells): parts.append(" | ".join(cells))
        return "\n\n".join(parts)

    if ext == "doc":
        import subprocess
        try:
            result = subprocess.run(
                ["antiword", str(path)], 
                capture_output=True, text=True, check=True
            )
            return result.stdout
        except FileNotFoundError:
            raise ValueError("Утилита 'antiword' не установлена. Невозможно прочитать .doc файл.")
        except subprocess.CalledProcessError:
            raise ValueError("Не удалось прочитать .doc файл. Возможно, он поврежден.")

    if ext in ("txt", "md", "csv"):
        return path.read_text(encoding="utf-8", errors="ignore")


    raise ValueError(f"Неподдерживаемый формат: .{ext}")

def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    return sum(a * b for a, b in zip(v1, v2))

async def semantic_chunk_text(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []

    import re
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    sentences = []
    for p in paragraphs:
        sents = re.split(r'(?<=[.!?])\s+', p)
        sentences.extend([s.strip() for s in sents if s.strip()])

    blocks = []
    current = ""
    for s in sentences:
        if len(current) + len(s) < 200:
            current = (current + " " + s).strip()
        else:
            if current: blocks.append(current)
            current = s
    if current: blocks.append(current)

    if len(blocks) <= 1:
        return blocks

    embeddings = await embedding_client.embed_passages(blocks)

    chunks = []
    current_chunk = [blocks[0]]
    current_len = len(blocks[0])

    for i in range(1, len(blocks)):
        sim = _cosine_similarity(embeddings[i-1], embeddings[i])
        dist = 1.0 - sim
        
        if dist > settings.chunk_semantic_threshold or current_len + len(blocks[i]) > settings.chunk_max_size:
            chunks.append(" ".join(current_chunk))
            current_chunk = [blocks[i]]
            current_len = len(blocks[i])
        else:
            current_chunk.append(blocks[i])
            current_len += len(blocks[i])

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks



def _get_embed_client() -> httpx.AsyncClient:
    global _embed_client
    if _embed_client is None or _embed_client.is_closed:
        _embed_client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0),
            limits=httpx.Limits(
                max_keepalive_connections=16,
                max_connections=32,
                keepalive_expiry=60.0,
            ),
        )
    return _embed_client


async def close_embedding_client() -> None:
    global _embed_client
    if _embed_client and not _embed_client.is_closed:
        await _embed_client.aclose()


class EmbeddingClient:
    def __init__(self) -> None:
        self.base_url = settings.embedding_base_url.rstrip("/")

    async def _embed(self, inputs: list[str]) -> list[list[float]]:
        if not inputs:
            return []
        inputs = [t if t.strip() else "." for t in inputs]

        results: list[list[float]] = []
        client = _get_embed_client()
        batch_size = 4  

        for i in range(0, len(inputs), batch_size):
            batch = inputs[i:i + batch_size]
            
            max_retries = 5  
            for attempt in range(max_retries):
                try:
                    response = await client.post(
                        f"{self.base_url}/embed",
                        json={"inputs": batch, "normalize": True},
                    )
                    response.raise_for_status()
                    results.extend(response.json())
                    break
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 429 and attempt < max_retries - 1:
                        wait_time = 2 ** (attempt + 1)
                        logger.warning(
                            f"TEI вернул 429 (Too Many Requests). "
                            f"Повтор через {wait_time} сек..."
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        raise exc  #
            
            await asyncio.sleep(0.2) 

        return results
    async def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return await self._embed([f"passage: {t}" for t in texts])

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._embed([f"query: {text.strip()}"])
        return vectors[0]


embedding_client = EmbeddingClient()


async def search_chunks(
    db: AsyncSession,
    agent_id: int,
    query: str,
    top_k: int | None = None,
    max_distance: float | None = None,
) -> list[tuple[DocumentChunk, float]]:
    """Возвращает (chunk, distance), отфильтрованные по порогу."""
    top_k = top_k or settings.rag_top_k
    max_distance = (
        max_distance if max_distance is not None
        else settings.rag_max_distance
    )

    query_emb = await embedding_client.embed_query(query)
    distance_expr = DocumentChunk.embedding.cosine_distance(query_emb)

    stmt = (
        select(DocumentChunk, distance_expr.label("distance"))
        .join(Document, Document.id == DocumentChunk.document_id)
        .join(AgentDocument, AgentDocument.document_id == Document.id)
        .where(
            AgentDocument.agent_id == agent_id,
            AgentDocument.is_enabled.is_(True),
            Document.status == "ready",
            DocumentChunk.embedding.is_not(None),
        )
        .order_by(distance_expr)
        .limit(top_k)
    )
    rows = (await db.execute(stmt)).all()
    return [(chunk, float(dist)) for chunk, dist in rows if dist <= max_distance]

async def index_document_background(doc_id: int, path: Path, ext: str) -> None:
    from app.database import AsyncSessionLocal
    from app.models import Document
    
    async with AsyncSessionLocal() as db:
        doc = await db.get(Document, doc_id)
        if doc is None:
            return 
        await index_document(db, doc, path, ext)
async def index_document(db: AsyncSession, doc: Document, path: Path, ext: str) -> None:
    try:
        text = extract_text(path, ext)
        doc.text = text
        doc.status = "processing"
        await db.commit()

        chunks = await semantic_chunk_text(text)
        
        if not chunks:
            doc.status = "ready"
            doc.error_message = None
            await db.commit()
            return

        embeddings = await embedding_client.embed_passages(chunks)

        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            db.add(
                DocumentChunk(
                    document_id=doc.id,
                    chunk_index=i,
                    content=chunk,
                    embedding_model=settings.embedding_model,
                    embedding=emb,
                    metadata_={"source": doc.filename},
                )
            )

        doc.status = "ready"
        doc.error_message = None
        await db.commit()

    except Exception as exc:
        logger.exception("Индексация документа %s не удалась", doc.id)
        doc.status = "failed"
        doc.error_message = str(exc)[:1000]
        await db.commit()
    finally:
        await publish_document_event(doc.id, doc.status, doc.uploaded_by)
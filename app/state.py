import asyncio

# Для чата
active_generations = {}

# Для документов
document_subscribers: list[asyncio.Queue] = []

async def publish_document_event(doc_id: int, status: str, user_id: int):
    for q in list(document_subscribers):
        await q.put({"id": doc_id, "status": status, "user_id": user_id})
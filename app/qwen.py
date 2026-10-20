from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class QwenError(Exception):
    """Ошибка при обращении к llama.cpp."""


class QwenClient:
    def __init__(self) -> None:
        self.base_url = settings.qwen_base_url.rstrip("/")
        self.timeout = httpx.Timeout(settings.qwen_timeout)
        self._client: httpx.AsyncClient | None = None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if settings.qwen_api_key:
            headers["Authorization"] = (
                f"Bearer {settings.qwen_api_key.get_secret_value()}"
            )
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers=self._headers(),
                limits=httpx.Limits(
                    max_keepalive_connections=16,
                    max_connections=32,
                    keepalive_expiry=60.0,
                ),
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def health(self) -> dict:
        try:
            client = await self._get_client()
            response = await client.get(f"{self.base_url}/health")
            return {
                "reachable": True,
                "status_code": response.status_code,
                "healthy": response.is_success,
            }
        except httpx.RequestError:
            return {"reachable": False, "healthy": False}


    def _build_messages(
        self,
        message: str,
        system_prompt: str | None,
        context: str | None,
    ) -> list[dict]:
        final_system = (system_prompt or "").strip()

        if context and context.strip():
            rag_instructions = (
                "Ты отвечаешь на вопрос строго на основе предоставленных "
                "фрагментов документов.\n\n"
                "Правила:\n"
                "1. Опирайся на фрагменты ниже.\n"
                "2. Если в них есть прямой ответ — раскрой его подробно: контекст, детали, "
                "цифры, выводы. Не ограничивайся одним предложением, если вопрос этого "
                "требует.\n"
                "3. Если в фрагментах ответа нет — так и скажи: «В предоставленных "
                "документах этой информации нет».\n"
            )
            final_system = (
                (final_system + "\n\n" if final_system else "")
                + rag_instructions
                + context
            )

        messages: list[dict] = []
        if final_system:
            messages.append({"role": "system", "content": final_system})
        messages.append({"role": "user", "content": message})
        return messages

    def _payload(
        self, messages: list[dict], stream: bool
    ) -> dict:
        payload = {
            "messages": messages,
            "max_tokens": settings.qwen_max_tokens,
            "temperature": settings.qwen_temperature,
            "stream": stream,
        }
        if settings.qwen_model:
            payload["model"] = settings.qwen_model
        return payload


    async def chat(
        self,
        message: str,
        system_prompt: str | None = None,
        context: str | None = None,
    ) -> str:
        messages = self._build_messages(message, system_prompt, context)
        payload = self._payload(messages, stream=False)

        try:
            client = await self._get_client()
            response = await client.post(
                f"{self.base_url}/v1/chat/completions", json=payload
            )
            response.raise_for_status()
            data = response.json()

            choice = data["choices"][0]
            answer = choice["message"]["content"]
            finish = choice.get("finish_reason")
            if finish == "length":
                logger.warning(
                    "Qwen обрезал ответ по max_tokens. usage=%s",
                    data.get("usage"),
                )

            if not isinstance(answer, str) or not answer.strip():
                raise QwenError("Модель вернула пустой ответ.")
            return answer.strip()

        except httpx.TimeoutException as exc:
            raise QwenError("Истекло время ожидания ответа Qwen.") from exc
        except httpx.HTTPStatusError as exc:
            raise QwenError(
                f"llama.cpp вернул HTTP {exc.response.status_code}."
            ) from exc
        except httpx.RequestError as exc:
            raise QwenError(
                "Не удалось подключиться к llama.cpp server."
            ) from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise QwenError(
                "Не удалось разобрать ответ llama.cpp."
            ) from exc


    async def chat_stream(
        self,
        message: str,
        system_prompt: str | None = None,
        context: str | None = None,
    ) -> AsyncIterator[str]:
        messages = self._build_messages(message, system_prompt, context)
        payload = self._payload(messages, stream=True)

        try:
            client = await self._get_client()
            async with client.stream(
                "POST",
                f"{self.base_url}/v1/chat/completions",
                json=payload,
            ) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise QwenError(
                        f"llama.cpp вернул HTTP {response.status_code}: "
                        f"{body[:200]!r}"
                    )

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    chunk_str = line[6:].strip()
                    if not chunk_str or chunk_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(chunk_str)
                    except json.JSONDecodeError:
                        continue
                    choice = (chunk.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield content
                    if choice.get("finish_reason") == "length":
                        logger.warning(
                            "Стрим Qwen завершён по max_tokens"
                        )

        except httpx.TimeoutException as exc:
            raise QwenError("Истекло время ожидания ответа Qwen.") from exc
        except httpx.RequestError as exc:
            raise QwenError(
                "Не удалось подключиться к llama.cpp server."
            ) from exc


qwen_client = QwenClient()
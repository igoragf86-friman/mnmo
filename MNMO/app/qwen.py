
import httpx

from app.config import settings


class QwenError(Exception):
    """Ошибка при обращении к llama.cpp."""


class QwenClient:
    def __init__(self) -> None:
        self.base_url = settings.qwen_base_url.rstrip("/")
        self.timeout = httpx.Timeout(settings.qwen_timeout)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}

        if settings.qwen_api_key:
            headers["Authorization"] = (
                f"Bearer {settings.qwen_api_key.get_secret_value()}"
            )

        return headers

    async def health(self) -> dict:
        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                headers=self._headers(),
            ) as client:
                response = await client.get(
                    f"{self.base_url}/health"
                )

            return {
                "reachable": True,
                "status_code": response.status_code,
                "healthy": response.is_success,
            }

        except httpx.RequestError:
            return {
                "reachable": False,
                "healthy": False,
            }

    async def chat(
        self,
        message: str,
        system_prompt: str | None = None,
    ) -> str:
        messages = []

        if system_prompt:
            messages.append({
                "role": "system",
                "content": system_prompt,
            })

        messages.append({
            "role": "user",
            "content": message,
        })

        payload = {
            "messages": messages,
            "max_tokens": settings.qwen_max_tokens,
            "temperature": settings.qwen_temperature,
            "stream": False,
        }

        if settings.qwen_model:
            payload["model"] = settings.qwen_model

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                headers=self._headers(),
            ) as client:
                response = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                )

                response.raise_for_status()
                data = response.json()

            answer = data["choices"][0]["message"]["content"]

            if not isinstance(answer, str) or not answer.strip():
                raise QwenError("Модель вернула пустой ответ.")

            return answer.strip()

        except httpx.TimeoutException as exc:
            raise QwenError(
                "Истекло время ожидания ответа Qwen."
            ) from exc

        except httpx.HTTPStatusError as exc:
            # Не возвращаем пользователю тело ответа сервера:
            # оно может содержать технические детали.
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


qwen_client = QwenClient()

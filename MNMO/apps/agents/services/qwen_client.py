
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class QwenAPIError(Exception):
    """Ошибка взаимодействия с API Qwen."""


class QwenClient:
    def __init__(self):
        self.base_url = os.environ["QWEN_API_URL"].rstrip("/")
        self.api_key = os.environ.get("QWEN_API_KEY", "")
        self.model = os.environ["QWEN_MODEL"]
        self.timeout = int(os.environ.get("QWEN_TIMEOUT", "180"))

    def chat(self, messages, temperature=0.2, max_tokens=512, model=None):
        url = f"{self.base_url}/chat/completions"

        payload = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        headers = {"Content-Type": "application/json"}

        if self.api_key and self.api_key != "not-needed":
            headers["Authorization"] = f"Bearer {self.api_key}"

        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.loads(
                    response.read().decode("utf-8")
                )
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise QwenAPIError(
                f"Qwen API вернул HTTP {exc.code}: {details}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise QwenAPIError(
                f"Не удалось подключиться к Qwen API: {exc}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise QwenAPIError(
                "Qwen API вернул некорректный JSON."
            ) from exc

        choices = result.get("choices", [])
        if not choices:
            raise QwenAPIError(
                f"В ответе Qwen отсутствует choices: {result}"
            )

        content = choices[0].get("message", {}).get("content", "")

        if not isinstance(content, str) or not content.strip():
            raise QwenAPIError(
                f"Qwen вернул пустой ответ: {result}"
            )

        return content.strip()

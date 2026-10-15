
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AI Agents API"
    app_env: str = "development"

    # Qwen / llama.cpp
    qwen_base_url: str = "http://172.16.121.114:8080"
    qwen_api_key: SecretStr | None = None
    qwen_model: str = ""
    qwen_timeout: float = Field(default=180.0, gt=0)
    qwen_max_tokens: int = Field(default=1024, gt=0)
    qwen_temperature: float = Field(default=0.2, ge=0, le=2)

    # PostgreSQL
    database_url: SecretStr
    embedding_dimension: int = Field(default=768, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

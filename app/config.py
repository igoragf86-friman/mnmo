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
    
    jwt_secret_key: SecretStr
    
    documents_dir: str = "var/documents"   
    documents_max_file_mb: int = Field(default=20, gt=0)
    # Qwen / llama.cpp
    qwen_base_url: str = "http://192.168.0.1:8080"
    qwen_api_key: SecretStr | None = None
    qwen_model: str = ""
    qwen_timeout: float = Field(default=180.0, gt=0)
    qwen_max_tokens: int = Field(default=4096, gt=0) 
    qwen_temperature: float = Field(default=0.35, ge=0, le=2)

    # PostgreSQL
    database_url: str
    
    embedding_base_url: str = "http://127.0.0.1:8081"
    embedding_model: str = "intfloat/multilingual-e5-base"
    embedding_dimension: int = 76

    rag_top_k: int = Field(default=4, gt=0, le=20)
    rag_max_distance: float = Field(default=0.55, ge=0, le=2)
    chunk_size: int = Field(default=500, gt=100)         
    chunk_tolerance: int = Field(default=200, ge=0)      
    chunk_overlap: int = Field(default=100, ge=0) 
@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):

    # =====================================================
    # Application
    # =====================================================

    APP_NAME: str = "Voice Assisted Coding Agent"

    APP_VERSION: str = "0.2.0"

    DEBUG: bool = True

    HOST: str = "127.0.0.1"

    PORT: int = 8000

    # =====================================================
    # Embedding
    # =====================================================

    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"

    EMBEDDING_BATCH_SIZE: int = 64

    # =====================================================
    # Reranker
    # =====================================================

    RERANK_MODEL: str = "BAAI/bge-reranker-base"

    # =====================================================
    # Retrieval
    # =====================================================

    DEFAULT_TOP_K: int = 10

    # =====================================================
    # Provider
    # =====================================================

    DEFAULT_PROVIDER: str = "ollama"

    # =====================================================
    # Ollama
    # =====================================================

    OLLAMA_HOST: str = "http://localhost:11434"

    OLLAMA_MODEL: str = "qwen3:8b"

    # =====================================================
    # OpenAI
    # =====================================================

    OPENAI_API_KEY: str = Field(
        default="",
        repr=False,
    )

    OPENAI_MODEL: str = "gpt-4o"

    # =====================================================
    # OpenRouter
    # =====================================================

    OPENROUTER_API_KEY: str = Field(
        default="",
        repr=False,
    )

    OPENROUTER_MODEL: str = "deepseek/deepseek-chat"

    # =====================================================
    # Google Gemini
    # =====================================================

    GEMINI_API_KEY: str = Field(
        default="",
        repr=False,
    )

    GEMINI_MODEL: str = "gemini-2.5-flash"

    # =====================================================
    # Anthropic Claude
    # =====================================================

    ANTHROPIC_API_KEY: str = Field(
        default="",
        repr=False,
    )

    ANTHROPIC_MODEL: str = "claude-3-7-sonnet-20250219"

    # =====================================================
    # Groq (Ultra-Fast Inference)
    # =====================================================

    GROQ_API_KEY: str = Field(
        default="",
        repr=False,
    )

    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # =====================================================
    # Qdrant
    # =====================================================

    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_COLLECTION: str = "repository_chunks"

    # =====================================================
    # Redis
    # =====================================================

    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    class Config:

        env_file = ".env"
        extra = "ignore"


settings = Settings()

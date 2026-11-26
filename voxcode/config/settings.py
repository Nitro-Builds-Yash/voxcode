"""Application settings, loaded from environment variables and `.env`."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ----------------------------------------------------------------- app
    APP_NAME: str = "VoxCode"
    APP_VERSION: str = "0.4.0"
    DEBUG: bool = False
    HOST: str = "127.0.0.1"
    PORT: int = 8000
    RELOAD: bool = False
    PRELOAD_MODELS: bool = True
    # Indexes, vectors and caches (kept out of the repositories you index).
    VOXCODE_HOME: str = "~/.voxcode"

    # ------------------------------------------------------------ security
    # Browser origins allowed to call the API (comma separated). Requests
    # from the server's own origin (the bundled UI) and requests with no
    # Origin header (VS Code extension, curl) are always allowed.
    ALLOWED_ORIGINS: str = ""
    # Optional shared secret. When set, every API call must send it in the
    # `X-VoxCode-Token` header (or `?token=` for WebSockets).
    API_TOKEN: str = Field(default="", repr=False)
    # Optional comma-separated list of directories that may be indexed or
    # edited. Empty means any directory except the filesystem root.
    ALLOWED_REPO_ROOTS: str = ""

    # ------------------------------------------------------------ indexing
    MAX_FILE_BYTES: int = 1_000_000
    MAX_CHUNK_LINES: int = 120
    WATCH_ENABLED: bool = False
    WATCH_DEBOUNCE_SECONDS: float = 2.0

    # ----------------------------------------------------------- embedding
    EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
    EMBEDDING_BATCH_SIZE: int = 64

    # ------------------------------------------------------------ reranker
    RERANK_ENABLED: bool = True
    RERANK_MODEL: str = "BAAI/bge-reranker-base"

    # ----------------------------------------------------------- retrieval
    DEFAULT_TOP_K: int = 8
    RETRIEVAL_CANDIDATES: int = 30
    GRAPH_EXPANSION: int = 4
    CONTEXT_MAX_TOKENS: int = 12_000

    # ------------------------------------------------------------ provider
    DEFAULT_PROVIDER: str = "ollama"
    PROVIDER_TIMEOUT_SECONDS: float = 120.0
    MAX_OUTPUT_TOKENS: int = 8192

    OLLAMA_HOST: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen3:8b"

    OPENAI_API_KEY: str = Field(default="", repr=False)
    OPENAI_MODEL: str = "gpt-4.1"

    OPENROUTER_API_KEY: str = Field(default="", repr=False)
    OPENROUTER_MODEL: str = "deepseek/deepseek-chat"

    GEMINI_API_KEY: str = Field(default="", repr=False)
    GEMINI_MODEL: str = "gemini-2.5-flash"

    ANTHROPIC_API_KEY: str = Field(default="", repr=False)
    ANTHROPIC_MODEL: str = "claude-sonnet-5-5"

    GROQ_API_KEY: str = Field(default="", repr=False)
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # -------------------------------------------------------------- qdrant
    # "local" stores vectors under VOXCODE_HOME; "server" talks to a Qdrant
    # server at QDRANT_HOST:QDRANT_PORT (used by docker-compose).
    QDRANT_MODE: str = "local"
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_COLLECTION_PREFIX: str = "voxcode"

    # -------------------------------------------------------------- memory
    # "auto" uses Redis when reachable and falls back to in-process memory.
    MEMORY_BACKEND: str = "auto"
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    MEMORY_TTL_SECONDS: int = 7 * 24 * 3600
    MEMORY_MAX_MESSAGES: int = 50

    # --------------------------------------------------------------- voice
    EMOTION_ENABLED: bool = True
    WHISPER_MODEL: str = "base"
    WHISPER_DEVICE: str = "cpu"
    WHISPER_COMPUTE_TYPE: str = "int8"
    WHISPER_BEAM_SIZE: int = 1
    # "auto" prefers Piper (neural) when PIPER_MODEL is set, else pyttsx3.
    TTS_ENGINE: str = "auto"
    PIPER_MODEL: str = ""

    # ---------------------------------------------------------- code edits
    EDIT_MAX_ATTEMPTS: int = 3
    TEST_TIMEOUT_SECONDS: int = 180

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _split(value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]

    @property
    def allowed_origins(self) -> list[str]:
        return self._split(self.ALLOWED_ORIGINS)

    @property
    def allowed_repo_roots(self) -> list[str]:
        return self._split(self.ALLOWED_REPO_ROOTS)


settings = Settings()

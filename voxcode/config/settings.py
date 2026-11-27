"""Application settings, loaded from environment variables and `.env`."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ----------------------------------------------------------------- app
    APP_NAME: str = "VoxCode"
    APP_VERSION: str = "0.8.1"
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
    # Model cascade: easy questions go to a small model first and escalate to
    # the main provider when the answer looks weak. Off while CASCADE_MODEL is empty.
    CASCADE_PROVIDER: str = ""  # defaults to DEFAULT_PROVIDER
    CASCADE_MODEL: str = ""  # e.g. qwen3:1.7b, claude-haiku-4-5, gpt-4.1-mini
    CASCADE_MAX_DIFFICULTY: float = 0.5
    CASCADE_MIN_GROUNDEDNESS: float = 0.75
    # Reuse answers to repeated questions until the index changes.
    ANSWER_CACHE: bool = True
    ANSWER_CACHE_SIZE: int = 256
    ANSWER_CACHE_TTL_SECONDS: int = 3600
    # Learned intent classifier; below this confidence the keyword rules decide.
    INTENT_MODEL: bool = True
    INTENT_MIN_CONFIDENCE: float = 0.5
    # Contextual retrieval: an LLM sentence per chunk at index time (one call per changed file, cached).
    CONTEXTUAL_CHUNKS: bool = False
    # Review (plans and patches in the browser): approved plans can be saved to Obsidian.
    OBSIDIAN_VAULT: str = ""
    OBSIDIAN_FOLDER: str = "VoxCode/Plans"
    # Port of the review server started by `voxcode hook` / `voxcode review` when VoxCode is not
    # already running. Fixed, so a review page survives between hook calls and works over SSH forwarding.
    REVIEW_PORT: int = 19432
    # Learned ranker (voxcode train-ranker): re-scores fused candidates when a model is trained.
    LEARNED_RANKER: bool = True
    # Train it automatically after indexing (self-supervised from docstrings; < 1 s for most repos).
    RANKER_AUTO_TRAIN: bool = True
    # Auto-training waits for this much feedback: docstring self-supervision alone
    # helped held-out docstring queries but not reliably real questions.
    RANKER_MIN_FEEDBACK: int = 10
    GRAPH_EXPANSION: int = 4
    CONTEXT_MAX_TOKENS: int = 12_000
    # Rewrite vague (spoken) questions into code-search queries with the LLM.
    QUERY_REWRITE: bool = False
    # Precise Python call graph with Jedi (falls back to name matching).
    PRECISE_CALL_GRAPH: bool = True
    # Recent commits indexed for "why/when/who changed" questions (0 disables).
    # Rust call graph: "auto" uses rust-analyzer from PATH when it works, else tree-sitter resolution.
    RUST_ANALYZER: str = "auto"
    RUST_ANALYZER_TIMEOUT: float = 60.0
    # Build the precise call graph after indexing, in the background, so the first question
    # doesn't wait for it (search uses name matching until it's ready).
    BACKGROUND_CALL_GRAPH: bool = True
    GIT_HISTORY_COMMITS: int = 300

    # ------------------------------------------------------------ provider
    DEFAULT_PROVIDER: str = "ollama"
    PROVIDER_TIMEOUT_SECONDS: float = 120.0
    MAX_OUTPUT_TOKENS: int = 8192
    # Refuse every provider that is not on this machine or the local network.
    LOCAL_ONLY: bool = False
    # Strip API keys, passwords and tokens from prompts sent to remote models.
    REDACT_SECRETS: bool = True
    # Optional USD prices per 1M tokens for cost tracking, as JSON:
    # {"claude-sonnet-5-5": {"input": 3, "output": 15, "cache_read": 0.3}}
    MODEL_PRICES: str = ""
    # Use the provider's native tool calling in agentic mode (JSON fallback otherwise).
    NATIVE_TOOL_CALLING: bool = True

    OLLAMA_HOST: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen3:8b"

    OPENAI_API_KEY: str = Field(default="", repr=False)
    OPENAI_MODEL: str = "gpt-4.1"
    # Any OpenAI-compatible server: LM Studio, vLLM, llama.cpp, Azure OpenAI...
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"

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
    # Hands-free listening: energy-based voice activity detection.
    # "auto": use the Rust extension (voxcode-native) when installed; "off": pure Python.
    NATIVE_ACCEL: str = "auto"
    VAD_SILENCE_SECONDS: float = 0.8
    VAD_ENERGY_THRESHOLD: float = 0.015
    # Optional wake word via openWakeWord (e.g. "hey_jarvis"); empty = push-to-talk only.
    WAKE_WORD: str = ""
    WAKE_WORD_THRESHOLD: float = 0.5

    # ---------------------------------------------------------- code edits
    EDIT_MAX_ATTEMPTS: int = 3
    TEST_TIMEOUT_SECONDS: int = 180
    # Draft a reviewable plan before multi-file edits.
    PLAN_EDITS: bool = True
    # "docker": run tests in a container with no network and resource limits;
    # "local": in a temporary copy on this machine; "auto": docker if available.
    TEST_SANDBOX: str = "auto"
    SANDBOX_IMAGE: str = "python:3.12-slim"
    SANDBOX_MEMORY: str = "2g"
    SANDBOX_CPUS: str = "2"
    # Used by branch + pull-request mode (/agent/apply with create_pr).
    GITHUB_TOKEN: str = Field(default="", repr=False)

    # ------------------------------------------------------- observability
    OTEL_ENABLED: bool = False
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://localhost:4318/v1/traces"
    OTEL_SERVICE_NAME: str = "voxcode"

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

from prometheus_client import Counter, Gauge, Histogram

# ==========================================================
# Requests
# ==========================================================

REQUESTS_TOTAL = Counter(
    "agent_requests_total",
    "Total number of agent requests",
)

REQUEST_ERRORS_TOTAL = Counter(
    "agent_request_errors_total",
    "Total number of failed agent requests",
)

ACTIVE_SESSIONS = Gauge(
    "agent_active_sessions",
    "Current active sessions",
)

# ==========================================================
# Pipeline Timing
# ==========================================================

PIPELINE_DURATION = Histogram(
    "pipeline_duration_seconds",
    "Entire agent pipeline execution time",
)

MEMORY_READ_DURATION = Histogram(
    "memory_read_duration_seconds",
    "Conversation history retrieval latency",
)

MEMORY_WRITE_DURATION = Histogram(
    "memory_write_duration_seconds",
    "Conversation history update latency",
)

RETRIEVAL_DURATION = Histogram(
    "retrieval_duration_seconds",
    "Hybrid retrieval latency",
)

RERANK_DURATION = Histogram(
    "rerank_duration_seconds",
    "Cross encoder reranking latency",
)

CONTEXT_DURATION = Histogram(
    "context_build_duration_seconds",
    "Context building latency",
)

PROMPT_DURATION = Histogram(
    "prompt_build_duration_seconds",
    "Prompt construction latency",
)

GENERATION_DURATION = Histogram(
    "generation_duration_seconds",
    "LLM generation latency",
    ["provider", "model"],
)

# ==========================================================
# Retrieval Quality
# ==========================================================

RETRIEVED_CHUNKS_COUNT = Histogram(
    "retrieved_chunks",
    "Number of retrieved chunks",
)

CONTEXT_SIZE_BYTES = Histogram(
    "context_size_bytes",
    "Context size in bytes",
)

PROMPT_SIZE_BYTES = Histogram(
    "prompt_size_bytes",
    "Prompt size in bytes",
)

# ==========================================================
# Tokens, cost and privacy
# ==========================================================

LLM_TOKENS_TOTAL = Counter(
    "llm_tokens_total",
    "LLM tokens by provider, model and kind (input, output, cache_read, cache_write)",
    ["provider", "model", "kind"],
)

LLM_COST_USD_TOTAL = Counter(
    "llm_cost_usd_total",
    "Estimated LLM spend in USD (only for models listed in MODEL_PRICES)",
    ["provider", "model"],
)

SECRETS_REDACTED_TOTAL = Counter(
    "secrets_redacted_total",
    "Secrets removed from prompts before they were sent to a remote provider",
)

TIME_TO_FIRST_TOKEN = Histogram(
    "time_to_first_token_seconds",
    "Latency from request start to the first streamed token",
    buckets=(0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5, 10),
)

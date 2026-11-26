from pydantic import BaseModel


class RetrievalResult(BaseModel):
    """A chunk returned by search, with the scores that ranked it."""

    chunk_id: str
    file_path: str
    symbol_name: str
    content: str
    start_line: int = 1
    end_line: int = 1
    language: str = ""
    chunk_type: str = ""
    score: float = 0.0
    rerank_score: float | None = None
    source: str = "hybrid"  # bm25 | semantic | hybrid | graph

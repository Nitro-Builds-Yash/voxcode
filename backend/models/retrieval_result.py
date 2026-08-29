from __future__ import annotations
from typing import Optional, Dict, Any

try:
    from pydantic import BaseModel
except ImportError:
    class BaseModel:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)


class RetrievalResult(BaseModel):
    chunk_id: str = ""
    file_path: str = ""
    symbol_name: str = ""
    content: str = ""
    score: float = 0.0
    source: str = "hybrid"

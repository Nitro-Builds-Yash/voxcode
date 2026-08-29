from __future__ import annotations
from typing import Optional, Dict, Any

try:
    from pydantic import BaseModel
except ImportError:
    class BaseModel:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)


class SearchResult(BaseModel):
    query: str = ""
    results: list = []
    total_matches: int = 0

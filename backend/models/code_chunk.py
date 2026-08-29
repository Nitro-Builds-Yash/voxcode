from __future__ import annotations
from typing import Optional, Dict, Any

try:
    from pydantic import BaseModel
except ImportError:
    class BaseModel:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)


class CodeChunk(BaseModel):
    chunk_id: str = ""
    file_path: str = ""
    language: str = "python"
    chunk_type: str = "code"
    symbol_name: str = ""
    start_line: int = 1
    end_line: int = 1
    content: str = ""

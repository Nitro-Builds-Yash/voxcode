from __future__ import annotations
from typing import Optional, Dict, Any

try:
    from pydantic import BaseModel
except ImportError:
    class BaseModel:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)


class CodeFile(BaseModel):
    file_path: str = ""
    language: str = "python"
    total_lines: int = 0
    size_bytes: int = 0

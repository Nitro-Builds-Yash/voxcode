from __future__ import annotations
from typing import Optional, Dict, Any

try:
    from pydantic import BaseModel
except ImportError:
    class BaseModel:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)


class CodeSymbol(BaseModel):
    name: str = ""
    symbol_type: str = "function"
    file_path: str = ""
    start_line: int = 1
    end_line: int = 1

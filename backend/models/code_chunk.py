from pydantic import BaseModel, Field


class CodeChunk(BaseModel):
    """A searchable slice of a source file (a symbol, or a window of lines)."""

    chunk_id: str
    file_path: str  # relative to the repository root, POSIX separators
    language: str
    chunk_type: str  # function | method | class | module | section
    symbol_name: str
    start_line: int
    end_line: int
    content: str
    calls: list[str] = Field(default_factory=list)

    def embedding_text(self) -> str:
        """Text sent to the embedding model: location + kind + code."""
        return f"{self.language} {self.chunk_type} {self.symbol_name} in {self.file_path}\n{self.content}"

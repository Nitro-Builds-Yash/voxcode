import uuid
import hashlib
from backend.models.code_chunk import CodeChunk


class ChunkBuilder:

    def build_chunks(
        self,
        file_path: str,
        content: str,
        symbols: list,
    ):

        chunks = []

        lines = content.splitlines()

        for symbol in symbols:
            start = symbol["start_line"]
            end = symbol["end_line"]

            chunk_text = "\n".join(lines[start - 1 : end])

            stable_chunk_id = hashlib.md5(
                (
                    file_path + symbol["type"] + symbol["name"] + str(start) + str(end)
                ).encode()
            ).hexdigest()

            chunk = CodeChunk(
                chunk_id=stable_chunk_id,
                file_path=file_path,
                language="python",
                chunk_type=symbol["type"],
                symbol_name=symbol["name"],
                start_line=start,
                end_line=end,
                content=chunk_text,
            )

            chunks.append(chunk)

        return chunks

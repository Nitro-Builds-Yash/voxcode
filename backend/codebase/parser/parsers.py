from __future__ import annotations
from pathlib import Path
from typing import Optional, Tuple, Any
from .base_parser import BaseParser


class GenericParser(BaseParser):
    language_name = "generic"

    def __init__(self, language_name: str = "generic"):
        self.language_name = language_name

    def parse_file(self, file_path: str):
        content = Path(file_path).read_text(encoding="utf-8", errors="ignore")
        return None, content


class PythonParser(BaseParser):
    language_name = "python"

    def __init__(self):
        self._tree_sitter = None
        try:
            from tree_sitter import Parser
            from tree_sitter_python import language
            self._parser = Parser()
            self._parser.language = language()
            self._tree_sitter = True
        except Exception:
            self._tree_sitter = False

    def parse_file(self, file_path: str):
        path = Path(file_path)
        content = path.read_text(encoding="utf-8", errors="ignore")
        if self._tree_sitter:
            try:
                tree = self._parser.parse(content.encode("utf-8"))
                return tree, content
            except Exception:
                pass
        try:
            import ast
            tree = ast.parse(content, filename=file_path)
            return tree, content
        except Exception:
            return None, content


class JavaParser(GenericParser):
    def __init__(self):
        super().__init__("java")


class JavaScriptParser(GenericParser):
    def __init__(self):
        super().__init__("javascript")


class TypeScriptParser(GenericParser):
    def __init__(self):
        super().__init__("typescript")


class TSXParser(GenericParser):
    def __init__(self):
        super().__init__("tsx")


class HTMLParser(GenericParser):
    def __init__(self):
        super().__init__("html")


class CSSParser(GenericParser):
    def __init__(self):
        super().__init__("css")


class MarkdownParser(GenericParser):
    def __init__(self):
        super().__init__("markdown")

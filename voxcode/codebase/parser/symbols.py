"""Symbol extraction (functions, methods, classes) for supported languages.

Uses tree-sitter grammars from `tree-sitter-language-pack` when installed and
falls back to Python's `ast` module for Python files.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass
from functools import cache

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str  # function | method | class
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    parent: str | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.parent}.{self.name}" if self.parent else self.name


# node type -> symbol kind, per tree-sitter grammar
_FUNCTION = "function"
_CLASS = "class"

SYMBOL_NODES: dict[str, dict[str, str]] = {
    "python": {
        "function_definition": _FUNCTION,
        "class_definition": _CLASS,
    },
    "javascript": {
        "function_declaration": _FUNCTION,
        "generator_function_declaration": _FUNCTION,
        "method_definition": _FUNCTION,
        "class_declaration": _CLASS,
    },
    "typescript": {
        "function_declaration": _FUNCTION,
        "method_definition": _FUNCTION,
        "class_declaration": _CLASS,
        "abstract_class_declaration": _CLASS,
        "interface_declaration": _CLASS,
    },
    "java": {
        "method_declaration": _FUNCTION,
        "constructor_declaration": _FUNCTION,
        "class_declaration": _CLASS,
        "interface_declaration": _CLASS,
        "enum_declaration": _CLASS,
        "record_declaration": _CLASS,
    },
    "go": {
        "function_declaration": _FUNCTION,
        "method_declaration": _FUNCTION,
        "type_spec": _CLASS,
    },
    "rust": {
        "function_item": _FUNCTION,
        "struct_item": _CLASS,
        "enum_item": _CLASS,
        "trait_item": _CLASS,
        "impl_item": _CLASS,
        "mod_item": _CLASS,  # only inline modules (`mod tests { ... }`)
        "union_item": _CLASS,
        "macro_definition": _FUNCTION,
    },
}
SYMBOL_NODES["tsx"] = SYMBOL_NODES["typescript"]

# `const handler = () => {...}` style definitions in JS/TS
_FUNCTION_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}


@cache
def _get_parser(language: str):
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError:
        return None
    try:
        return get_parser(language)
    except Exception as exc:  # unknown grammar or broken install
        logger.debug("No tree-sitter grammar for %s: %s", language, exc)
        return None


def extract_symbols(content: str, language: str) -> list[Symbol]:
    """Return the symbols defined in `content`, outermost first."""
    if language not in SYMBOL_NODES:
        return []

    parser = _get_parser(language)
    if parser is not None:
        try:
            tree = parser.parse(content.encode("utf-8"))
            return _from_tree_sitter(tree.root_node, language)
        except Exception as exc:
            logger.debug("tree-sitter failed for %s: %s", language, exc)

    if language == "python":
        return _from_python_ast(content)
    return []


def _node_name(node) -> str | None:
    if node.type == "mod_item" and node.child_by_field_name("body") is None:
        return None  # `mod foo;` only declares a file
    for field in ("name", "type"):
        child = node.child_by_field_name(field)
        if child is not None and child.text:
            name = child.text.decode("utf-8", errors="replace")
            if node.type == "impl_item":
                # `impl<T> Store<T>` / `impl fmt::Display for crate::db::Store` -> Store
                name = name.split("<", 1)[0].rsplit("::", 1)[-1].strip()
            return name
    return None


def _from_tree_sitter(root, language: str) -> list[Symbol]:
    kinds = SYMBOL_NODES[language]
    symbols: list[Symbol] = []

    def visit(node, parent_class: str | None, start_override: int | None = None) -> None:
        if node.type == "decorated_definition":
            definition = node.child_by_field_name("definition")
            if definition is not None:
                visit(definition, parent_class, node.start_point[0] + 1)
                return

        kind = kinds.get(node.type)
        name = None

        if kind is None and node.type == "variable_declarator":
            value = node.child_by_field_name("value")
            if value is not None and value.type in _FUNCTION_VALUES:
                kind = _FUNCTION

        if kind is not None:
            name = _node_name(node)

        if kind is not None and name:
            symbol_kind = "method" if kind == _FUNCTION and parent_class else kind
            symbols.append(
                Symbol(
                    name=name,
                    kind=symbol_kind,
                    start_line=start_override or node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    parent=parent_class if symbol_kind == "method" else None,
                )
            )
            if kind == _CLASS:
                parent_class = name
            elif kind == _FUNCTION:
                # Nested helpers belong to their function's chunk.
                return

        for child in node.children:
            visit(child, parent_class)

    visit(root, None)
    return symbols


def _from_python_ast(content: str) -> list[Symbol]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []

    symbols: list[Symbol] = []

    def visit(body, parent_class: str | None) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "method" if parent_class else "function"
                symbols.append(
                    Symbol(
                        node.name,
                        kind,
                        _start_line(node),
                        node.end_lineno or node.lineno,
                        parent_class,
                    )
                )
            elif isinstance(node, ast.ClassDef):
                symbols.append(Symbol(node.name, "class", _start_line(node), node.end_lineno or node.lineno))
                visit(node.body, node.name)

    visit(tree.body, None)
    return symbols


def _start_line(node) -> int:
    decorators = getattr(node, "decorator_list", [])
    return min([node.lineno] + [d.lineno for d in decorators])

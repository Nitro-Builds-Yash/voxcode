from __future__ import annotations
import ast
from typing import List, Dict, Any


class SymbolExtractor:
    """Extracts function and class symbols from AST or Tree-sitter nodes."""

    def extract(self, root_node, language: str = "python") -> List[Dict[str, Any]]:
        symbols: List[Dict[str, Any]] = []
        if root_node is None:
            return symbols

        # Standard Python AST node handling
        if isinstance(root_node, ast.AST):
            for node in ast.walk(root_node):
                if isinstance(node, (ast.FunctionDef, getattr(ast, "AsyncFunctionDef", ast.FunctionDef))):
                    end_lineno = getattr(node, "end_lineno", node.lineno + 10)
                    symbols.append({
                        "name": node.name,
                        "type": "function",
                        "start_line": node.lineno,
                        "end_line": end_lineno,
                    })
                elif isinstance(node, ast.ClassDef):
                    end_lineno = getattr(node, "end_lineno", node.lineno + 15)
                    symbols.append({
                        "name": node.name,
                        "type": "class",
                        "start_line": node.lineno,
                        "end_line": end_lineno,
                    })
            return symbols

        # Tree-sitter node handling
        try:
            def traverse(node):
                if hasattr(node, "type"):
                    if node.type in ["function_definition", "async_function_definition", "function_declaration", "method_definition"]:
                        name_node = node.child_by_field_name("name")
                        name = name_node.text.decode("utf-8") if name_node and hasattr(name_node, "text") else "func"
                        symbols.append({
                            "name": name,
                            "type": "function",
                            "start_line": node.start_point[0] + 1,
                            "end_line": node.end_point[0] + 1,
                        })
                    elif node.type in ["class_definition", "class_declaration"]:
                        name_node = node.child_by_field_name("name")
                        name = name_node.text.decode("utf-8") if name_node and hasattr(name_node, "text") else "class"
                        symbols.append({
                            "name": name,
                            "type": "class",
                            "start_line": node.start_point[0] + 1,
                            "end_line": node.end_point[0] + 1,
                        })
                    for child in node.children:
                        traverse(child)

            traverse(root_node)
        except Exception:
            pass

        return symbols

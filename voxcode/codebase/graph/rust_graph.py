"""Call edges for Rust code, resolved through the module system.

Name matching would link `store.get()` to every `get` in the workspace. This
resolver parses each file with tree-sitter and follows what rustc follows:

* module paths from the file layout (`src/a/b.rs`, `src/a/mod.rs`, inline
  `mod tests { ... }`), per crate (every `Cargo.toml` is a crate; `src/bin/*`,
  `tests/*`, `examples/*`, `benches/*` are crate roots of their own that can
  reach the library by its crate name),
* `use` declarations: nested lists, `as` aliases, `self`, globs,
* `crate::` / `self::` / `super::` / `Self::` / `Type::assoc()` paths,
* `self.method()` inside an `impl`, and `x.method()` when exactly one type
  in the workspace defines that method (and it is not a common std name).

When rust-analyzer is installed, `rust_analyzer.py` is used instead and
this module is the fallback.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from voxcode.models.code_chunk import CodeChunk

logger = logging.getLogger(__name__)

Module = tuple[str, ...]  # (crate key, *module segments)

# Method names so common in std that a unique workspace match is a guess.
_STD_METHODS = {
    "len", "is_empty", "clone", "iter", "iter_mut", "into_iter", "unwrap", "expect", "map", "and_then",
    "ok", "err", "ok_or", "unwrap_or", "unwrap_or_default", "unwrap_or_else", "push", "pop", "get",
    "get_mut", "insert", "remove", "contains", "contains_key", "to_string", "to_owned", "into", "from",
    "as_ref", "as_mut", "as_str", "collect", "filter", "fold", "next", "send", "recv", "lock", "read",
    "write", "join", "split", "trim", "parse", "fmt", "eq", "cmp", "hash", "default", "drop", "extend",
    "entry", "or_insert", "keys", "values", "first", "last", "take", "borrow", "borrow_mut", "with_capacity",
    "clear", "sort", "starts_with", "ends_with", "replace", "chars", "bytes", "lines", "flush", "await",
}  # fmt: skip


@dataclass
class _Crate:
    key: str
    lib_name: str | None = None  # name other crate roots use to reach the library


@dataclass
class _FileScope:
    """`use` imports and glob imports visible in one module."""

    aliases: dict[str, list[str]] = field(default_factory=dict)
    globs: list[list[str]] = field(default_factory=list)


@dataclass
class _Defs:
    functions: dict[tuple[Module, str], CodeChunk] = field(default_factory=dict)
    methods: dict[tuple[str, str], list[CodeChunk]] = field(default_factory=lambda: defaultdict(list))
    types: dict[Module, set[str]] = field(default_factory=lambda: defaultdict(set))
    modules: set[Module] = field(default_factory=set)


def _crate_for(rel: str, crate_dirs: list[str]) -> str | None:
    best = None
    for directory in crate_dirs:
        prefix = "" if directory == "." else directory + "/"
        if rel.startswith(prefix) and (best is None or len(directory) > len(best)):
            best = directory
    return best


def module_of(rel: str, crate_dir: str) -> Module:
    """`src/a/b.rs` -> (crate, "a", "b"); separate roots get their own key."""
    inside = PurePosixPath(rel if crate_dir == "." else rel[len(crate_dir) + 1 :])
    parts = list(inside.with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
        if parts and parts[0] == "bin":
            return (f"{crate_dir}#bin/{'/'.join(parts[1:])}",)
        if parts in (["lib"], ["main"]):
            return (crate_dir if parts == ["lib"] else f"{crate_dir}#main",)
        if parts and parts[-1] == "mod":
            parts = parts[:-1]
        return (crate_dir, *parts)
    if parts and parts[0] in ("tests", "examples", "benches"):
        return (f"{crate_dir}#{'/'.join(parts)}",)
    return (crate_dir, *parts)


def _text(node) -> str:
    return node.text.decode("utf-8", errors="replace") if node is not None and node.text else ""


def _use_entries(node, prefix: list[str], out: list[tuple[str, list[str]]]) -> None:
    """Flatten a use tree into (local name, path) pairs; local "*" for globs."""
    kind = node.type
    if kind in ("identifier", "crate", "self", "super"):
        name = _text(node)
        out.append(
            (prefix[-1] if name == "self" and prefix else name, [*prefix, name] if name != "self" else prefix)
        )
    elif kind == "scoped_identifier":
        path = [*prefix, *_text(node).split("::")]
        out.append((path[-1], path))
    elif kind == "use_as_clause":
        path = [*prefix, *_text(node.child_by_field_name("path")).split("::")]
        alias = _text(node.child_by_field_name("alias"))
        if path and path[-1] == "self":
            path = path[:-1]
        out.append((alias, path))
    elif kind == "use_wildcard":
        base = _text(node).removesuffix("::*").removesuffix("*")
        out.append(("*", [*prefix, *[p for p in base.split("::") if p]]))
    elif kind == "scoped_use_list":
        path_node = node.child_by_field_name("path")
        nested = [*prefix, *_text(path_node).split("::")] if path_node is not None else prefix
        _use_entries(node.child_by_field_name("list"), nested, out)
    elif kind == "use_list":
        for child in node.named_children:
            _use_entries(child, prefix, out)


_TABLE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_NAME = re.compile(r"""^\s*name\s*=\s*["']([^"']+)["']""")


def _crate_name(manifest: Path) -> str | None:
    """[lib] name, else [package] name (a tiny parser: tomllib needs 3.11)."""
    names: dict[str, str] = {}
    table = ""
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        header = _TABLE.match(line)
        if header:
            table = header.group(1).strip()
            continue
        match = _NAME.match(line)
        if match and table in ("lib", "package") and table not in names:
            names[table] = match.group(1)
    return names.get("lib") or names.get("package")


# Calls in raw macro tokens: `recv.method(` (groups 1, 2) or `path::to::func(` (group 3).
_TOKEN_CALL = re.compile(
    r"(\bself|\b[a-z_][A-Za-z0-9_]*|\))\s*\.\s*([A-Za-z_]\w*)\s*\("
    r"|(?<![\w.:])((?:[A-Za-z_]\w*::)*[A-Za-z_]\w*)\s*\("
)


class RustResolver:
    def __init__(self, repo: Path, chunks: list[CodeChunk]):
        from voxcode.codebase.parser.symbols import _get_parser

        self.repo = repo
        self.parser = _get_parser("rust")
        self.rust_chunks = [c for c in chunks if c.language == "rust"]
        self.by_file: dict[str, list[CodeChunk]] = defaultdict(list)
        for chunk in self.rust_chunks:
            self.by_file[chunk.file_path].append(chunk)
        self.crates = self._find_crates()
        self.defs = _Defs()
        self.scopes: dict[Module, _FileScope] = defaultdict(_FileScope)
        self.trees: dict[str, Any] = {}
        self.file_module: dict[str, Module] = {}

    # ---------------------------------------------------------- crates
    def _find_crates(self) -> dict[str, _Crate]:
        crates: dict[str, _Crate] = {}
        for manifest in sorted(self.repo.rglob("Cargo.toml")):
            rel_dir = manifest.parent.relative_to(self.repo).as_posix()
            if "/target/" in f"/{rel_dir}/":
                continue
            name = _crate_name(manifest)
            crates[rel_dir] = _Crate(rel_dir, name.replace("-", "_") if name else None)
        if not crates:  # loose .rs files: treat the repo as one crate
            crates["."] = _Crate(".")
        return crates

    def _crate_dir(self, rel: str) -> str:
        return _crate_for(rel, list(self.crates)) or "."

    # ------------------------------------------------------ definitions
    def _locate(self, rel: str, line: int, kinds=("function", "method", "class")) -> CodeChunk | None:
        return min(
            (
                c
                for c in self.by_file.get(rel, [])
                if c.chunk_type in kinds and c.start_line <= line <= c.end_line
            ),
            key=lambda c: (c.end_line - c.start_line, c.start_line),
            default=None,
        )

    def collect(self) -> None:
        if self.parser is None:
            return
        for rel in sorted(self.by_file):
            try:
                source = (self.repo / rel).read_bytes()
            except OSError:
                continue
            tree = self.parser.parse(source)
            self.trees[rel] = tree
            module = module_of(rel, self._crate_dir(rel))
            self.file_module[rel] = module
            self.defs.modules.add(module)
            self._collect_items(rel, tree.root_node, module, None)

    def _collect_items(self, rel: str, node, module: Module, impl_type: str | None) -> None:
        for child in node.named_children:
            kind = child.type
            if kind == "function_item":
                name = _text(child.child_by_field_name("name"))
                chunk = self._locate(rel, child.start_point[0] + 1, ("function", "method"))
                if chunk is None:
                    continue
                if impl_type:
                    self.defs.methods[(impl_type, name)].append(chunk)
                else:
                    self.defs.functions[(module, name)] = chunk
            elif kind in ("struct_item", "enum_item", "trait_item", "union_item", "type_item"):
                self.defs.types[module].add(_text(child.child_by_field_name("name")))
                if kind == "trait_item":  # default methods
                    body = child.child_by_field_name("body")
                    if body is not None:
                        self._collect_items(rel, body, module, _text(child.child_by_field_name("name")))
            elif kind == "impl_item":
                type_name = _text(child.child_by_field_name("type")).split("<", 1)[0].rsplit("::", 1)[-1]
                body = child.child_by_field_name("body")
                if body is not None and type_name:
                    self._collect_items(rel, body, module, type_name)
            elif kind == "mod_item":
                body = child.child_by_field_name("body")
                if body is not None:
                    inner = (*module, _text(child.child_by_field_name("name")))
                    self.defs.modules.add(inner)
                    self._collect_items(rel, body, inner, None)
            elif kind == "use_declaration":
                entries: list[tuple[str, list[str]]] = []
                argument = child.child_by_field_name("argument")
                if argument is not None:
                    _use_entries(argument, [], entries)
                scope = self.scopes[module]
                for local, path in entries:
                    if local == "*":
                        scope.globs.append(path)
                    elif path:
                        scope.aliases[local] = path

    # -------------------------------------------------------- resolving
    def _absolute(
        self, segments: list[str], module: Module, impl_type: str | None, depth: int = 0
    ) -> list[str] | None:
        """Turn a path as written in `module` into absolute (crate, ...) segments."""
        if not segments or depth > 8:
            return None
        head, rest = segments[0], segments[1:]
        crate_key = module[0]
        if head == "crate":
            return [crate_key, *rest]
        if head == "self":
            return [*module, *rest]
        if head == "super":
            parent = list(module[:-1]) if len(module) > 1 else list(module)
            return self._absolute(["self", *rest], tuple(parent), impl_type, depth + 1) if rest else parent
        if head == "Self" and impl_type:
            return ["<type>", impl_type, *rest]
        alias = self.scopes[module].aliases.get(head)
        if alias is not None:
            if alias[0] in ("crate", "self", "super"):
                target = self._absolute(alias, module, impl_type, depth + 1)
            else:  # another crate of the workspace, or a 2015-style crate-relative path
                target = self._external(alias) or [module[0], *alias]
            return None if target is None else [*target, *rest]
        if (*module, head) in self.defs.modules:
            return [*module, head, *rest]
        root_dir = crate_key.split("#", 1)[0]
        crate = self.crates.get(root_dir)
        if crate and crate.lib_name == head:
            return [root_dir, *rest]
        for other in self.crates.values():
            if other.lib_name == head:
                return [other.key, *rest]
        if head in self.defs.types.get(module, set()):
            return ["<type>", head, *rest]
        for glob in self.scopes[module].globs:  # `use super::*` brings types into scope
            base = self._absolute(glob, module, impl_type, depth + 1) if glob else None
            if base and head in self.defs.types.get(tuple(base), set()):
                return ["<type>", head, *rest]
        return None

    def _external(self, alias: list[str]) -> list[str] | None:
        for crate in self.crates.values():
            if crate.lib_name == alias[0]:
                return [crate.key, *alias[1:]]
        return None

    def _lookup(self, path: list[str]) -> list[CodeChunk]:
        """Definitions an absolute path can name."""
        if not path:
            return []
        if path[0] == "<type>" and len(path) == 3:
            return list(self.defs.methods.get((path[1], path[2]), []))
        *module, name = path
        found = self.defs.functions.get((tuple(module), name))
        if found is not None:
            return [found]
        if len(module) >= 2:  # Type::assoc_fn where Type lives in `module[:-1]`
            return list(self.defs.methods.get((module[-1], name), []))
        return []

    def resolve_call(self, function, module: Module, impl_type: str | None) -> list[CodeChunk]:
        kind = function.type
        if kind == "identifier":
            name = _text(function)
            local = self.defs.functions.get((module, name))
            if local is not None:
                return [local]
            path = self._absolute([name], module, impl_type)
            if path:
                hits = self._lookup(path)
                if hits:
                    return hits
            for glob in self.scopes[module].globs:
                base = self._absolute(glob, module, impl_type) or self._absolute(
                    ["crate", *glob], module, impl_type
                )
                if base:
                    hits = self._lookup([*base, name])
                    if hits:
                        return hits
            # Parent modules' items are not in scope in Rust, but the prelude of a
            # test module usually is `use super::*` (handled by globs above).
            return []
        if kind == "scoped_identifier":
            segments = [s for s in _text(function).split("::") if s]
            segments = [s.split("<", 1)[0] for s in segments]
            path = self._absolute(segments, module, impl_type)
            return self._lookup(path) if path else []
        if kind == "field_expression":
            method = _text(function.child_by_field_name("field"))
            receiver = function.child_by_field_name("value")
            if receiver is not None and receiver.type == "self" and impl_type:
                own = self.defs.methods.get((impl_type, method))
                if own:
                    return list(own)
            if method in _STD_METHODS:
                return []
            owners = [chunks for (_, name), chunks in self.defs.methods.items() if name == method]
            if len(owners) == 1 and len(owners[0]) == 1:
                return list(owners[0])
            return []
        if kind == "generic_function":
            inner = function.child_by_field_name("function")
            return self.resolve_call(inner, module, impl_type) if inner is not None else []
        return []

    def _macro_calls(self, text: str, module: Module, impl_type: str | None) -> list[CodeChunk]:
        found: list[CodeChunk] = []
        for match in _TOKEN_CALL.finditer(text):
            receiver, method, path = match.groups()
            if receiver is not None:  # `x.method(` / `self.method(`
                if receiver == "self" and impl_type and (impl_type, method) in self.defs.methods:
                    hits = list(self.defs.methods[(impl_type, method)])
                elif method in _STD_METHODS:
                    hits = []
                else:
                    owners = [c for (_, name), c in self.defs.methods.items() if name == method]
                    hits = list(owners[0]) if len(owners) == 1 and len(owners[0]) == 1 else []
            else:
                segments = [s.split("<", 1)[0] for s in path.split("::") if s]
                if len(segments) == 1:
                    local = self.defs.functions.get((module, segments[0]))
                    resolved = (
                        [*module, segments[0]] if local else self._absolute(segments, module, impl_type)
                    )
                else:
                    resolved = self._absolute(segments, module, impl_type)
                hits = self._lookup(resolved) if resolved else []
            found += [h for h in hits if h not in found]
        return found

    # ----------------------------------------------------------- edges
    def edges_for(self, files: set[str]) -> dict[str, list[str]]:
        edges: dict[str, list[str]] = defaultdict(list)
        for rel in sorted(files):
            tree = self.trees.get(rel)
            module = self.file_module.get(rel)
            if tree is None or module is None:
                continue
            self._walk_calls(rel, tree.root_node, module, None, edges)
        return dict(edges)

    def _walk_calls(self, rel: str, node, module: Module, impl_type: str | None, edges) -> None:
        for child in node.named_children:
            kind = child.type
            if kind == "impl_item":
                type_name = _text(child.child_by_field_name("type")).split("<", 1)[0].rsplit("::", 1)[-1]
                self._walk_calls(rel, child, module, type_name or impl_type, edges)
                continue
            if kind == "trait_item":
                self._walk_calls(rel, child, module, _text(child.child_by_field_name("name")), edges)
                continue
            if kind == "mod_item":
                body = child.child_by_field_name("body")
                if body is not None:
                    self._walk_calls(
                        rel, body, (*module, _text(child.child_by_field_name("name"))), None, edges
                    )
                continue
            if kind == "macro_invocation":
                # Macro arguments are raw tokens to tree-sitter (`assert_eq!(s.count(), 1)`).
                body = next((c for c in child.named_children if c.type == "token_tree"), None)
                caller = self._locate(rel, child.start_point[0] + 1, ("function", "method", "module"))
                if body is not None and caller is not None:
                    for target in self._macro_calls(_text(body), module, impl_type):
                        if (
                            target.chunk_id != caller.chunk_id
                            and target.chunk_id not in edges[caller.chunk_id]
                        ):
                            edges[caller.chunk_id].append(target.chunk_id)
                continue
            if kind == "call_expression":
                function = child.child_by_field_name("function")
                if function is not None:
                    line = child.start_point[0] + 1
                    caller = self._locate(rel, line, ("function", "method", "module"))
                    if caller is not None:
                        for target in self.resolve_call(function, module, impl_type):
                            if (
                                target.chunk_id != caller.chunk_id
                                and target.chunk_id not in edges[caller.chunk_id]
                            ):
                                edges[caller.chunk_id].append(target.chunk_id)
            self._walk_calls(rel, child, module, impl_type, edges)


def build_rust_edges(repo: Path, chunks: list[CodeChunk], targets: set[str]) -> dict[str, list[str]]:
    """Edges from functions in `targets` (repo-relative .rs paths)."""
    if not targets:
        return {}
    from voxcode.codebase.graph import rust_analyzer

    analyzer_edges = rust_analyzer.call_edges(repo, chunks, targets)
    if analyzer_edges is not None:
        return analyzer_edges
    resolver = RustResolver(repo, chunks)
    if resolver.parser is None:
        return {}
    resolver.collect()
    return resolver.edges_for(targets)

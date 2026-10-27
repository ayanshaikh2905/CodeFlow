"""Python AST project parser."""

import ast
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ParsedModule:
    path: Path
    relative_path: str
    module_name: str
    source: str
    tree: ast.Module
    import_targets: dict[str, str] = field(default_factory=dict)
    module_aliases: dict[str, str] = field(default_factory=dict)
    symbol_ids: dict[str, str] = field(default_factory=dict)
    ast_ids: dict[int, str] = field(default_factory=dict)
    definitions: list[ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef] = field(
        default_factory=list
    )


DEFAULT_EXCLUDED = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "tests",
    "venv",
}


class PythonProjectParser:
    """Parse Python files with stdlib ``ast`` and stable relative paths."""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def parse(self, root: Path, excluded: set[str] | None = None) -> list[ParsedModule]:
        self.warnings = []
        root = root.resolve()
        skip = DEFAULT_EXCLUDED | (excluded or set())
        modules: list[ParsedModule] = []
        for path in sorted(root.rglob("*.py")):
            relative = path.relative_to(root)
            if any(part in skip for part in relative.parts):
                continue
            try:
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(path))
            except (OSError, SyntaxError, UnicodeDecodeError) as exc:
                self.warnings.append(f"Skipped {relative.as_posix()}: {exc}")
                continue
            module_name = self.module_name(relative)
            parsed = ParsedModule(path, relative.as_posix(), module_name, source, tree)
            self._collect_definitions(parsed)
            self._collect_imports(parsed)
            modules.append(parsed)
        return modules

    @staticmethod
    def module_name(relative_path: Path) -> str:
        parts = list(relative_path.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)

    def _collect_definitions(self, parsed: ParsedModule) -> None:
        module_id = self.node_id(parsed, parsed.module_name)
        parsed.symbol_ids[parsed.module_name] = module_id

        def visit(body: list[ast.stmt], prefix: str, inside_function: bool = False) -> None:
            for statement in body:
                if isinstance(statement, ast.ClassDef):
                    qualname = f"{prefix}.{statement.name}"
                    parsed.symbol_ids[qualname] = self.node_id(parsed, qualname)
                    parsed.ast_ids[id(statement)] = parsed.symbol_ids[qualname]
                    parsed.definitions.append(statement)
                    visit(statement.body, qualname)
                elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    local = ".<locals>" if inside_function else "."
                    qualname = f"{prefix}{local}{statement.name}"
                    parsed.symbol_ids[qualname] = self.node_id(parsed, qualname)
                    parsed.ast_ids[id(statement)] = parsed.symbol_ids[qualname]
                    parsed.definitions.append(statement)
                    visit(statement.body, qualname, inside_function=True)

        visit(parsed.tree.body, parsed.module_name)

    @staticmethod
    def node_id(parsed: ParsedModule, qualname: str) -> str:
        return f"{parsed.relative_path}::{qualname}"

    @staticmethod
    def _collect_imports(parsed: ParsedModule) -> None:
        for statement in parsed.tree.body:
            if isinstance(statement, ast.Import):
                for alias in statement.names:
                    local_name = alias.asname or alias.name.split(".")[0]
                    resolved = alias.name if alias.asname else alias.name.split(".")[0]
                    parsed.module_aliases[local_name] = resolved
            elif isinstance(statement, ast.ImportFrom):
                base = resolve_relative_import(
                    parsed.module_name, statement.module, statement.level
                )
                for alias in statement.names:
                    if alias.name == "*":
                        continue
                    local_name = alias.asname or alias.name
                    parsed.import_targets[local_name] = (
                        f"{base}.{alias.name}" if base else alias.name
                    )


def resolve_relative_import(module_name: str, module: str | None, level: int) -> str:
    """Resolve an import's module path without touching the filesystem."""
    if level == 0:
        return module or ""
    package = module_name.split(".")[:-1]
    keep = max(0, len(package) - level + 1)
    prefix = package[:keep]
    if module:
        prefix.extend(module.split("."))
    return ".".join(prefix)

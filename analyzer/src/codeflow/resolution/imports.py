"""Index and resolve local Python symbols and import aliases."""

import ast
from dataclasses import dataclass

from codeflow.parsing.python import ParsedModule


@dataclass
class SymbolIndex:
    modules: dict[str, ParsedModule]
    ids: dict[str, str]

    @classmethod
    def build(cls, modules: list[ParsedModule]) -> "SymbolIndex":
        return cls(
            modules={module.module_name: module for module in modules},
            ids={
                symbol: node_id
                for module in modules
                for symbol, node_id in module.symbol_ids.items()
            },
        )

    def resolve(
        self, module: ParsedModule, expression: ast.expr, class_name: str | None = None
    ) -> str | None:
        """Resolve direct names, imported aliases, module attributes and self methods."""
        if isinstance(expression, ast.Name):
            if class_name and expression.id in self._class_methods(class_name):
                return self.ids.get(f"{class_name}.{expression.id}")
            if expression.id in module.import_targets:
                return self._resolve_qualified(module.import_targets[expression.id])
            return self._resolve_qualified(f"{module.module_name}.{expression.id}")
        if isinstance(expression, ast.Attribute):
            value = expression.value
            if isinstance(value, ast.Name):
                if value.id in {"self", "cls"} and class_name:
                    return self.ids.get(f"{class_name}.{expression.attr}")
                if value.id in module.module_aliases:
                    return self._resolve_qualified(
                        f"{module.module_aliases[value.id]}.{expression.attr}"
                    )
                imported = module.import_targets.get(value.id)
                if imported:
                    return self._resolve_qualified(f"{imported}.{expression.attr}")
            return None
        return None

    def qualified_target(self, module: ParsedModule, expression: ast.expr) -> str | None:
        """Return the best qualified import path represented by an expression."""
        if isinstance(expression, ast.Name):
            return module.import_targets.get(expression.id, f"{module.module_name}.{expression.id}")
        if isinstance(expression, ast.Attribute):
            if isinstance(expression.value, ast.Name):
                root = module.module_aliases.get(expression.value.id)
                if root:
                    return f"{root}.{expression.attr}"
                root = module.import_targets.get(expression.value.id)
                if root:
                    return f"{root}.{expression.attr}"
        return None

    def _resolve_qualified(self, target: str) -> str | None:
        if target in self.ids:
            return self.ids[target]
        # ``import package.module`` aliases resolve to module nodes.
        if target in self.modules:
            return self.modules[target].symbol_ids.get(target)
        return None

    def _class_methods(self, class_name: str) -> set[str]:
        module_name = class_name.rsplit(".", 1)[0]
        parsed = self.modules.get(module_name)
        if not parsed:
            return set()
        prefix = f"{class_name}."
        return {
            qualname[len(prefix) :]
            for qualname in parsed.symbol_ids
            if qualname.startswith(prefix) and ".<locals>." not in qualname
        }

"""FastAPI route, router mount and dependency detector."""

import ast

from codeflow.detectors.common import literal_string, make_edge
from codeflow.parsing.python import ParsedModule
from codeflow.resolution.imports import SymbolIndex
from codeflow.schema import Edge, GraphNode, Route

ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def detect_fastapi(
    modules: list[ParsedModule], index: SymbolIndex
) -> tuple[list[GraphNode], list[Edge], list[Route]]:
    """Find decorated endpoints and FastAPI Depends relationships."""
    nodes: list[GraphNode] = []
    edges: list[Edge] = []
    routes: list[Route] = []
    mounted_prefixes = _router_mounts(modules, index)

    for module in modules:
        for definition in module.definitions:
            if not isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            handler_id = module.ast_ids[id(definition)]
            handler_qualname = next(
                (name for name, node_id in module.symbol_ids.items() if node_id == handler_id),
                definition.name,
            )
            class_name = handler_qualname.rsplit(".", 1)[0] if "." in handler_qualname else None
            for decorator in definition.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(
                    decorator.func, ast.Attribute
                ):
                    continue
                method = decorator.func.attr.lower()
                if method not in ROUTE_METHODS:
                    continue
                router_name = (
                    decorator.func.value.id if isinstance(decorator.func.value, ast.Name) else ""
                )
                local_prefix = _router_prefix(module, router_name)
                target_name = module.import_targets.get(
                    router_name, f"{module.module_name}.{router_name}"
                )
                prefixes = mounted_prefixes.get(target_name, [""])
                route_path = literal_string(decorator.args[0]) if decorator.args else "/"
                path = _join_paths(local_prefix, route_path or "/")
                for prefix in prefixes:
                    full_path = _join_paths(prefix, path)
                    route_qualname = f"route[{method.upper()} {full_path}]"
                    route_id = f"{module.relative_path}::{route_qualname}"
                    nodes.append(
                        GraphNode(
                            id=route_id,
                            kind="route",
                            name=f"{method.upper()} {full_path}",
                            qualname=route_qualname,
                            file=module.relative_path,
                            line_start=definition.lineno,
                            line_end=getattr(definition, "end_lineno", definition.lineno),
                            meta={"method": method.upper(), "path": full_path},
                        )
                    )
                    route = Route(
                        id=route_id,
                        method=method.upper(),
                        path=full_path,
                        handler_node_id=handler_id,
                    )
                    routes.append(route)
                    edges.append(make_edge(route_id, handler_id, "calls"))
            for argument in (*definition.args.defaults, *definition.args.kw_defaults):
                if argument is None:
                    continue
                for child in ast.walk(argument):
                    if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Name):
                        continue
                    if child.func.id != "Depends" or not child.args:
                        continue
                    target = index.resolve(module, child.args[0], class_name)
                    if target:
                        edges.append(make_edge(handler_id, target, "depends_on"))
    return nodes, edges, routes


def _router_mounts(modules: list[ParsedModule], index: SymbolIndex) -> dict[str, list[str]]:
    mounts: dict[str, list[str]] = {}
    for module in modules:
        for statement in ast.walk(module.tree):
            if not isinstance(statement, ast.Call) or not isinstance(statement.func, ast.Attribute):
                continue
            if statement.func.attr != "include_router" or not statement.args:
                continue
            router = statement.args[0]
            if not isinstance(router, ast.Name):
                continue
            target = module.import_targets.get(router.id, f"{module.module_name}.{router.id}")
            prefix = ""
            for keyword in statement.keywords:
                if keyword.arg == "prefix":
                    prefix = literal_string(keyword.value) or ""
            mounts.setdefault(target, []).append(prefix)
    return mounts


def _router_prefix(module: ParsedModule, router_name: str) -> str:
    if not router_name:
        return ""
    for statement in module.tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        value = statement.value
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        if not any(isinstance(target, ast.Name) and target.id == router_name for target in targets):
            continue
        if not isinstance(value, ast.Call):
            continue
        if not (isinstance(value.func, ast.Name) and value.func.id == "APIRouter"):
            continue
        for keyword in value.keywords:
            if keyword.arg == "prefix":
                return literal_string(keyword.value) or ""
    return ""


def _join_paths(prefix: str, path: str) -> str:
    result = f"{prefix.rstrip('/')}/{path.lstrip('/')}"
    return result if result.startswith("/") else f"/{result}"

"""Build a typed CodeFlow graph from a Python project."""

import ast
import builtins
from pathlib import Path

from codeflow.detectors.common import make_edge
from codeflow.detectors.fastapi import detect_fastapi
from codeflow.detectors.http import detect_http
from codeflow.detectors.sqlalchemy import detect_sqlalchemy
from codeflow.parsing.python import ParsedModule, PythonProjectParser
from codeflow.resolution.imports import SymbolIndex
from codeflow.schema import Edge, Graph, GraphNode


class GraphBuilder:
    """Coordinate the Python parser, symbol resolution and framework detectors."""

    def __init__(self, excluded: set[str] | None = None) -> None:
        self.excluded = excluded or set()
        self.parser = PythonProjectParser()

    def analyze(self, project: Path) -> Graph:
        root = project.resolve()
        modules = self.parser.parse(root, self.excluded)
        index = SymbolIndex.build(modules)
        nodes: dict[str, GraphNode] = {}
        edges: dict[str, Edge] = {}
        warnings: list[str] = list(self.parser.warnings)
        module_node_ids: dict[str, str] = {}

        for module in modules:
            module_id = module.symbol_ids[module.module_name]
            module_node_ids[module.module_name] = module_id
            nodes[module_id] = GraphNode(
                id=module_id,
                kind="module",
                name=module.module_name.rsplit(".", 1)[-1] or module.module_name,
                qualname=module.module_name,
                file=module.relative_path,
            )
            for definition in module.definitions:
                node_id = module.ast_ids[id(definition)]
                qualname = next(
                    (name for name, candidate in module.symbol_ids.items() if candidate == node_id),
                    definition.name,
                )
                nodes[node_id] = GraphNode(
                    id=node_id,
                    kind="class" if isinstance(definition, ast.ClassDef) else "function",
                    name=definition.name,
                    qualname=qualname,
                    file=module.relative_path,
                    line_start=definition.lineno,
                    line_end=getattr(definition, "end_lineno", definition.lineno),
                    meta={"async": isinstance(definition, ast.AsyncFunctionDef)}
                    if isinstance(definition, ast.AsyncFunctionDef)
                    else {},
                )

        model_nodes, model_edges, model_tables = detect_sqlalchemy(modules)
        for node in model_nodes:
            nodes[node.id] = node
        for edge in model_edges:
            edges[edge.id] = edge

        for module in modules:
            source_id = module_node_ids[module.module_name]
            for local_name, target in module.import_targets.items():
                target_id = index._resolve_qualified(target)
                if target_id:
                    edges[make_edge(source_id, target_id, "imports").id] = make_edge(
                        source_id, target_id, "imports"
                    )
                else:
                    target_module = target.rsplit(".", 1)[0]
                    target_id = module_node_ids.get(target_module)
                    if target_id:
                        edge = make_edge(
                            source_id, target_id, "imports", meta={"symbol": local_name}
                        )
                        edges[edge.id] = edge
            for local_name, target in module.module_aliases.items():
                target_id = module_node_ids.get(target)
                if target_id:
                    edge = make_edge(source_id, target_id, "imports", meta={"alias": local_name})
                    edges[edge.id] = edge

            for definition in module.definitions:
                if not isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                source_id = module.ast_ids[id(definition)]
                qualname = next(
                    (
                        name
                        for name, candidate in module.symbol_ids.items()
                        if candidate == source_id
                    ),
                    definition.name,
                )
                class_name = _enclosing_class(module, qualname)
                local_types = _local_types(definition, module, index)
                for call in _calls_in_function(definition):
                    target_id = index.resolve(module, call.func, class_name)
                    if target_id:
                        edge = make_edge(source_id, target_id, "calls")
                        edges[edge.id] = edge
                        continue
                    if isinstance(call.func, ast.Attribute):
                        receiver_type = _attribute_receiver_type(
                            call.func.value, class_name, local_types
                        )
                        method_id = (
                            index.ids.get(f"{receiver_type}.{call.func.attr}")
                            if receiver_type
                            else None
                        )
                        if method_id:
                            edge = make_edge(source_id, method_id, "calls")
                            edges[edge.id] = edge
                            continue
                        if _is_http_call(module, call):
                            continue
                        if _is_known_external_call(module, call):
                            continue
                        unresolved_name = call.func.attr
                    elif isinstance(call.func, ast.Name):
                        unresolved_name = call.func.id
                        if (
                            unresolved_name in module.import_targets
                            or unresolved_name in module.symbol_ids
                            or hasattr(builtins, unresolved_name)
                            or _is_external_factory_alias(module, unresolved_name)
                        ):
                            continue
                    else:
                        continue
                    placeholder_id = f"unresolved:{unresolved_name}"
                    nodes.setdefault(
                        placeholder_id,
                        GraphNode(
                            id=placeholder_id,
                            kind="external",
                            name=unresolved_name,
                            qualname=placeholder_id,
                            meta={"unresolved": True},
                        ),
                    )
                    edge = make_edge(
                        source_id,
                        placeholder_id,
                        "calls",
                        kind="inferred",
                        meta={"unresolved": True, "line": call.lineno},
                    )
                    edges[edge.id] = edge

        route_nodes, route_edges, routes = detect_fastapi(modules, index)
        for node in route_nodes:
            nodes[node.id] = node
        for edge in route_edges:
            edges[edge.id] = edge
        http_nodes, http_edges = detect_http(modules)
        for node in http_nodes:
            nodes[node.id] = node
        for edge in http_edges:
            edges[edge.id] = edge

        if not modules:
            warnings.append("No Python source files were found in the project.")
        if not routes:
            warnings.append("No FastAPI route decorators were detected.")
        if not model_tables:
            warnings.append("No SQLAlchemy declarative models were detected.")
        return Graph(
            nodes=list(nodes.values()),
            edges=list(edges.values()),
            routes=routes,
            meta={"root": str(root), "warnings": warnings, "model_tables": model_tables},
        )


def _calls_in_function(
    definition: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.Call]:
    calls: list[ast.Call] = []

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            if node is definition:
                for child in node.body:
                    self.visit(child)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            if node is definition:
                for child in node.body:
                    self.visit(child)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return

        def visit_Call(self, node: ast.Call) -> None:
            calls.append(node)
            self.generic_visit(node)

    Visitor().visit(definition)
    return calls


def _enclosing_class(module: ParsedModule, qualname: str) -> str | None:
    class_names = {
        name
        for definition in module.definitions
        if isinstance(definition, ast.ClassDef)
        for name, node_id in module.symbol_ids.items()
        if node_id == module.ast_ids[id(definition)]
    }
    pieces = qualname.split(".")
    while len(pieces) > 1:
        pieces.pop()
        candidate = ".".join(pieces)
        if candidate in class_names:
            return candidate
    return None


def _local_types(
    definition: ast.FunctionDef | ast.AsyncFunctionDef,
    module: ParsedModule,
    index: SymbolIndex,
) -> dict[str, str]:
    result: dict[str, str] = {}
    arguments = [*definition.args.posonlyargs, *definition.args.args, *definition.args.kwonlyargs]
    for argument in arguments:
        if argument.annotation:
            target = index.resolve(module, argument.annotation)
            if target:
                result[argument.arg] = index.qualified_name(target) or ""
    for statement in ast.walk(definition):
        if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            target = index.resolve(module, statement.annotation)
            if target:
                result[statement.target.id] = index.qualified_name(target) or ""
        elif (
            isinstance(statement, ast.Assign)
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, (ast.Name, ast.Attribute))
        ):
            target = index.resolve(module, statement.value.func)
            if target:
                class_name = index.qualified_name(target)
                if class_name:
                    for name in statement.targets:
                        if isinstance(name, ast.Name):
                            result[name.id] = class_name
    return result


def _attribute_receiver_type(
    receiver: ast.expr, class_name: str | None, local_types: dict[str, str]
) -> str | None:
    if isinstance(receiver, ast.Name):
        if receiver.id in {"self", "cls"}:
            return class_name
        return local_types.get(receiver.id)
    return None


def _is_http_call(module: ParsedModule, call: ast.Call) -> bool:
    if not isinstance(call.func, ast.Attribute) or call.func.attr not in {
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "request",
        "send",
    }:
        return False
    base = call.func.value
    if isinstance(base, ast.Name):
        module_name = module.module_aliases.get(base.id, "")
        imported = module.import_targets.get(base.id, "")
        return module_name in {"requests", "httpx", "aiohttp"} or imported.split(".")[0] in {
            "requests",
            "httpx",
            "aiohttp",
        }
    if isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
        return module.module_aliases.get(base.value.id, "") in {"requests", "httpx", "aiohttp"}
    return False


def _is_known_external_call(module: ParsedModule, call: ast.Call) -> bool:
    if not isinstance(call.func, ast.Attribute):
        return False
    method = call.func.attr
    if method in {
        "add",
        "append",
        "close",
        "commit",
        "delete",
        "filter",
        "first",
        "get",
        "json",
        "options",
        "query",
        "raise_for_status",
        "refresh",
        "startswith",
    }:
        return True
    base = call.func.value
    if isinstance(base, ast.Name) and base.id in module.module_aliases:
        # Calls into imported packages are library behavior; HTTP requests are
        # represented separately by the outbound-call detector.
        return True
    return False


def _is_external_factory_alias(module: ParsedModule, name: str) -> bool:
    for statement in module.tree.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            continue
        value = statement.value
        if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name):
            continue
        if value.func.id in module.import_targets:
            return True
    return False

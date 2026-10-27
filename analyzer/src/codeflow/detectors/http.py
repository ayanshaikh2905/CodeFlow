"""Outbound HTTP client detector."""

import ast
from urllib.parse import urlparse

from codeflow.detectors.common import literal_string, make_edge
from codeflow.parsing.python import ParsedModule
from codeflow.schema import Edge, GraphNode

HTTP_MODULES = {"requests", "httpx", "aiohttp"}
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "request", "send"}


def detect_http(modules: list[ParsedModule]) -> tuple[list[GraphNode], list[Edge]]:
    """Find common requests/httpx/aiohttp calls and literal target URLs."""
    nodes: list[GraphNode] = []
    edges: list[Edge] = []
    for module in modules:
        local_http_clients = _http_client_variables(module)
        for definition in module.definitions:
            if not isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            source_id = module.ast_ids[id(definition)]
            for call in _calls_in_function(definition):
                method, is_http = _http_call(module, call, local_http_clients)
                if not is_http or not method:
                    continue
                url = _url_argument(call)
                host = urlparse(url).netloc if url else "dynamic"
                label = url or f"http://{host}"
                node_id = f"{module.relative_path}::external:{label}"
                nodes.append(
                    GraphNode(
                        id=node_id,
                        kind="external",
                        name=host or label,
                        qualname=f"external:{label}",
                        file=module.relative_path,
                        line_start=call.lineno,
                        line_end=getattr(call, "end_lineno", call.lineno),
                        meta={"url": url, "method": method.upper()},
                    )
                )
                edges.append(
                    make_edge(
                        source_id,
                        node_id,
                        "http_call",
                        meta={"method": method.upper(), "url": url},
                    )
                )
    return nodes, edges


def _http_client_variables(module: ParsedModule) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(module.tree):
        if isinstance(node, ast.arg) and node.annotation:
            annotation = ast.unparse(node.annotation)
            if any(name in annotation for name in HTTP_MODULES):
                result.add(node.arg)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            annotation = ast.unparse(node.annotation)
            if any(name in annotation for name in HTTP_MODULES):
                result.add(node.target.id)
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            target_path = ast.unparse(node.value.func)
            if any(name in target_path for name in HTTP_MODULES):
                result.update(target.id for target in node.targets if isinstance(target, ast.Name))
    return result


def _http_call(module: ParsedModule, call: ast.Call, clients: set[str]) -> tuple[str | None, bool]:
    if not isinstance(call.func, ast.Attribute) or call.func.attr not in HTTP_METHODS:
        return None, False
    base = call.func.value
    if isinstance(base, ast.Name):
        module_name = module.module_aliases.get(base.id, "")
        if module_name in HTTP_MODULES:
            return call.func.attr, True
        imported = module.import_targets.get(base.id, "")
        if imported.split(".")[0] in HTTP_MODULES:
            return call.func.attr, True
        if base.id in clients:
            return call.func.attr, True
    if isinstance(base, ast.Attribute):
        root = base.value.id if isinstance(base.value, ast.Name) else ""
        module_name = module.module_aliases.get(root, root)
        if module_name in HTTP_MODULES:
            return call.func.attr, True
    return call.func.attr, False


def _url_argument(call: ast.Call) -> str | None:
    for argument in call.args:
        literal = literal_string(argument)
        if literal:
            return literal
    for keyword in call.keywords:
        if keyword.arg in {"url", "uri"}:
            return literal_string(keyword.value)
    return None


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

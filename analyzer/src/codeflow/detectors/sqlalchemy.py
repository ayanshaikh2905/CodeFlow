"""SQLAlchemy ORM model and read/write detector."""

import ast

from codeflow.detectors.common import literal_string, make_edge
from codeflow.parsing.python import ParsedModule
from codeflow.schema import Edge, GraphNode


def detect_sqlalchemy(
    modules: list[ParsedModule],
) -> tuple[list[GraphNode], list[Edge], dict[str, str]]:
    """Detect declarative models, tables and obvious ORM access operations."""
    nodes: list[GraphNode] = []
    edges: list[Edge] = []
    model_ids: dict[str, str] = {}
    table_ids: dict[str, str] = {}
    model_tables: dict[str, str] = {}
    for module in modules:
        for definition in module.definitions:
            if not isinstance(definition, ast.ClassDef) or not _is_model(definition):
                continue
            model_id = module.ast_ids[id(definition)]
            table_name = _table_name(definition) or definition.name.lower()
            model_ids[f"{module.module_name}.{definition.name}"] = model_id
            model_ids[definition.name] = model_id
            model_tables[model_id] = table_name
            nodes.append(
                GraphNode(
                    id=model_id,
                    kind="model",
                    name=definition.name,
                    qualname=next(
                        (
                            name
                            for name, node_id in module.symbol_ids.items()
                            if node_id == model_id
                        ),
                        definition.name,
                    ),
                    file=module.relative_path,
                    line_start=definition.lineno,
                    line_end=getattr(definition, "end_lineno", definition.lineno),
                    meta={"table": table_name},
                )
            )
            table_id = f"{module.relative_path}::table:{table_name}"
            table_ids[table_name] = table_id
            nodes.append(
                GraphNode(
                    id=table_id,
                    kind="table",
                    name=table_name,
                    qualname=f"table:{table_name}",
                    file=module.relative_path,
                    meta={"table": table_name},
                )
            )
            edges.append(make_edge(model_id, table_id, "depends_on"))

    return_models: dict[str, str] = {}
    for module in modules:
        for definition in module.definitions:
            if (
                not isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef))
                or not definition.returns
            ):
                continue
            return_model_id = _resolve_model_expr(definition.returns, model_ids, {})
            if return_model_id:
                qualname = next(
                    (
                        name
                        for name, node_id in module.symbol_ids.items()
                        if node_id == module.ast_ids[id(definition)]
                    ),
                    definition.name,
                )
                return_models[qualname] = return_model_id

    for module in modules:
        for definition in module.definitions:
            if not isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            function_id = module.ast_ids[id(definition)]
            local_models = _local_model_variables(definition, module, model_ids, return_models)
            written_models: set[str] = set()
            for call in _calls_in_function(definition):
                access = _model_access(call, model_ids, local_models)
                if access is None:
                    continue
                model_id, direction = access
                edges.append(make_edge(function_id, model_id, direction))
                if direction == "writes":
                    written_models.add(model_id)
            for statement in ast.walk(definition):
                targets = (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else [statement.target]
                    if isinstance(statement, (ast.AnnAssign, ast.AugAssign))
                    else []
                )
                for target in targets:
                    if not isinstance(target, ast.Attribute) or not isinstance(
                        target.value, ast.Name
                    ):
                        continue
                    field_model_id = local_models.get(target.value.id)
                    if field_model_id and field_model_id not in written_models:
                        edges.append(
                            make_edge(
                                function_id,
                                field_model_id,
                                "writes",
                                kind="inferred",
                                meta={"reason": "ORM instance field assignment"},
                            )
                        )
                        written_models.add(field_model_id)
            constructed_models = {
                resolved
                for call in _calls_in_function(definition)
                if isinstance(call.func, (ast.Name, ast.Attribute))
                if (resolved := _resolve_model_expr(call.func, model_ids, local_models))
            }
            for model_id in constructed_models - written_models:
                edges.append(
                    make_edge(
                        function_id,
                        model_id,
                        "writes",
                        kind="inferred",
                        meta={"reason": "model instance constructed; persistence not confirmed"},
                    )
                )
    return nodes, edges, model_tables


def _is_model(definition: ast.ClassDef) -> bool:
    if definition.name == "Base":
        return False
    if any(
        isinstance(statement, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__tablename__"
            for target in statement.targets
        )
        for statement in definition.body
    ):
        return True
    return any(
        _simple_name(base).endswith("Base") or _simple_name(base) == "DeclarativeBase"
        for base in definition.bases
    )


def _table_name(definition: ast.ClassDef) -> str | None:
    for statement in definition.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name) and target.id == "__tablename__":
                    return literal_string(statement.value)
    return None


def _model_access(
    call: ast.Call, model_ids: dict[str, str], local_models: dict[str, str]
) -> tuple[str, str] | None:
    function = call.func
    name = (
        function.id
        if isinstance(function, ast.Name)
        else function.attr
        if isinstance(function, ast.Attribute)
        else ""
    )
    if name == "select" and call.args:
        return _access(_resolve_model_expr(call.args[0], model_ids, local_models), "reads")
    if isinstance(function, ast.Attribute) and name in {"query", "get"} and call.args:
        return _access(_resolve_model_expr(call.args[0], model_ids, local_models), "reads")
    if isinstance(function, ast.Name) and name in {"insert", "update", "delete"} and call.args:
        return _access(_resolve_model_expr(call.args[0], model_ids, local_models), "writes")
    if isinstance(function, ast.Attribute) and name == "add" and call.args:
        value = call.args[0]
        return _access(_resolve_model_expr(value, model_ids, local_models), "writes")
    if isinstance(function, ast.Attribute) and name == "delete" and call.args:
        return _access(_resolve_model_expr(call.args[0], model_ids, local_models), "writes")
    return None


def _access(model_id: str | None, direction: str) -> tuple[str, str] | None:
    return (model_id, direction) if model_id else None


def _resolve_model_expr(
    expression: ast.expr, model_ids: dict[str, str], local_models: dict[str, str]
) -> str | None:
    if isinstance(expression, ast.Name):
        return model_ids.get(expression.id) or local_models.get(expression.id)
    if isinstance(expression, ast.Attribute):
        return model_ids.get(expression.attr)
    if isinstance(expression, ast.Call):
        return _resolve_model_expr(expression.func, model_ids, local_models)
    return None


def _local_model_variables(
    definition: ast.FunctionDef | ast.AsyncFunctionDef,
    module: ParsedModule,
    model_ids: dict[str, str],
    return_models: dict[str, str],
) -> dict[str, str]:
    local_models: dict[str, str] = {}
    for statement in ast.walk(definition):
        if (
            isinstance(statement, ast.Assign)
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, (ast.Name, ast.Attribute))
        ):
            model_id = _resolve_model_expr(statement.value.func, model_ids, {})
            if model_id:
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        local_models[target.id] = model_id
            elif isinstance(statement.value.func, ast.Name):
                function_name = statement.value.func.id
                target_name = module.import_targets.get(
                    function_name, f"{module.module_name}.{function_name}"
                )
                return_model = return_models.get(target_name)
                if return_model:
                    for target in statement.targets:
                        if isinstance(target, ast.Name):
                            local_models[target.id] = return_model
    return local_models


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


def _simple_name(expression: ast.expr) -> str:
    if isinstance(expression, ast.Name):
        return expression.id
    if isinstance(expression, ast.Attribute):
        return expression.attr
    return ""

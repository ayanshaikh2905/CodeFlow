"""Fixtures for core static graph detectors and scenario traversal."""

import json
from pathlib import Path

from typer.testing import CliRunner

from codeflow.cli import app
from codeflow.graph.builder import GraphBuilder


def test_demo_app_detects_routes_calls_models_tables_and_http() -> None:
    root = Path(__file__).parents[1] / "demo_app"
    graph = GraphBuilder().analyze(root)
    assert {f"{route.method} {route.path}" for route in graph.routes} >= {
        "GET /users/{user_id}",
        "POST /orders",
        "GET /orders/{order_id}",
        "POST /orders/{order_id}/pay",
    }
    node_names = {node.name for node in graph.nodes}
    assert {"User", "Order", "OrderItem", "Product", "users", "orders", "order_items", "products"} <= node_names
    assert any(edge.type == "http_call" and edge.meta.get("url") == "https://payments.example.test/charges" for edge in graph.edges)
    node_by_id = {node.id: node for node in graph.nodes}
    assert any(
        edge.type == "depends_on" and node_by_id[edge.target].name == "get_current_user"
        for edge in graph.edges
    )
    assert any(
        edge.type == "reads" and node_by_id[edge.target].name == "Product"
        for edge in graph.edges
    )
    assert any(
        edge.type == "writes"
        and node_by_id[edge.target].name == "OrderItem"
        and edge.kind == "inferred"
        for edge in graph.edges
    )
    assert graph.model_dump_json()
    json.loads(graph.model_dump_json())


def test_python_parser_resolves_import_aliases_and_relative_paths(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "api.py").write_text(
        "from .service import run as execute\n\ndef endpoint():\n    return execute()\n",
        encoding="utf-8",
    )
    graph = GraphBuilder().analyze(tmp_path)
    call = next(edge for edge in graph.edges if edge.type == "calls")
    assert call.source == "pkg/api.py::pkg.api.endpoint"
    assert call.target == "pkg/service.py::pkg.service.run"


def test_analyze_cli_writes_graph_json(tmp_path: Path) -> None:
    (tmp_path / "api.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/health')\ndef health():\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    output = tmp_path / "out" / "graph.json"
    result = CliRunner().invoke(app, [str(tmp_path), "-o", str(output)])
    assert result.exit_code == 0, result.output
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["routes"][0]["path"] == "/health"
    assert "Wrote" in result.output



"""Cycle-safe ordering and depth behavior for scenario traversal."""

from codeflow.schema import Edge, Graph, GraphNode, Route
from codeflow.tracing.scenario import trace_scenario
from typer.testing import CliRunner

from codeflow.cli import app


def test_scenario_breadth_first_cycle_safe_and_depth_limited() -> None:
    route = Route(
        id="api.py::route[GET /x]",
        method="GET",
        path="/x",
        handler_node_id="api.py::handler",
    )
    graph = Graph(
        nodes=[
            GraphNode(id="api.py::handler", kind="function", name="handler", qualname="handler"),
            GraphNode(id="api.py::one", kind="function", name="one", qualname="one"),
            GraphNode(id="api.py::two", kind="function", name="two", qualname="two"),
        ],
        routes=[route],
        edges=[
            Edge(
                id="route-handler",
                source=route.id,
                target=route.handler_node_id,
                type="calls",
                kind="parsed",
            ),
            Edge(id="a", source="api.py::handler", target="api.py::one", type="calls", kind="parsed"),
            Edge(id="b", source="api.py::handler", target="api.py::two", type="calls", kind="parsed"),
            Edge(id="c", source="api.py::one", target="api.py::two", type="calls", kind="parsed"),
            Edge(id="d", source="api.py::two", target="api.py::handler", type="calls", kind="parsed"),
        ],
    )
    scenario = trace_scenario(graph, "GET /x", max_depth=2)
    assert [step.node_id for step in scenario.steps] == [
        route.id,
        "api.py::handler",
        "api.py::one",
        "api.py::two",
    ]
    assert [step.depth for step in scenario.steps] == [0, 1, 2, 2]
    assert trace_scenario(graph, "GET /x", max_depth=1).steps[-1].node_id == "api.py::handler"


def test_trace_cli_prints_summary_and_saves_json(tmp_path) -> None:
    (tmp_path / "api.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/health')\ndef health():\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    output = tmp_path / "scenario.json"
    result = CliRunner().invoke(
        app, ["trace", str(tmp_path), "--route", "GET /health", "-o", str(output)]
    )
    assert result.exit_code == 0, result.output
    assert "GET /health" in result.output
    assert '"steps"' in output.read_text(encoding="utf-8")

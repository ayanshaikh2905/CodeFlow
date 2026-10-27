"""FastAPI read-only endpoints for an analyzed project."""

from pathlib import Path
from typing import Any

from codeflow.graph.builder import GraphBuilder
from codeflow.schema import Graph, GraphNode
from codeflow.tracing.scenario import trace_scenario
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles


def create_app(project: Path, web_dist: Path | None = None) -> FastAPI:
    """Analyze ``project`` once and serve its graph, routes, scenarios and source."""
    root = project.resolve()
    graph = GraphBuilder().analyze(root)
    app = FastAPI(title="CodeFlow API", version="0.1.0")
    app.state.project_root = root
    app.state.graph = graph

    @app.get("/api/graph")
    def get_graph() -> Graph:
        return graph

    @app.get("/api/routes")
    def get_routes() -> list[dict[str, str]]:
        return [route.model_dump() for route in graph.routes]

    @app.get("/api/scenario")
    def get_scenario(
        route: str = Query(..., description='Route selector, e.g. "POST /orders".'),
        max_depth: int = Query(default=8, ge=0, le=64),
    ) -> dict[str, Any]:
        try:
            return trace_scenario(graph, route, max_depth=max_depth).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/source")
    def get_source(node_id: str = Query(...)) -> dict[str, Any]:
        node = next((candidate for candidate in graph.nodes if candidate.id == node_id), None)
        if node is None:
            raise HTTPException(status_code=404, detail="Node not found")
        return _source_payload(root, graph, node)

    distribution = (web_dist or Path(__file__).parents[3] / "web" / "dist").resolve()
    if distribution.is_dir():
        app.mount("/", StaticFiles(directory=distribution, html=True), name="viewer")
    return app


def _source_payload(root: Path, graph: Graph, node: GraphNode) -> dict[str, Any]:
    incoming = [edge.model_dump() for edge in graph.edges if edge.target == node.id]
    outgoing = [edge.model_dump() for edge in graph.edges if edge.source == node.id]
    source: str | None = None
    if node.file:
        source_path = (root / node.file).resolve()
        if not source_path.is_relative_to(root):
            raise HTTPException(status_code=400, detail="Invalid source path")
        try:
            lines = source_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise HTTPException(status_code=404, detail="Source file not found") from exc
        start = node.line_start or 1
        end = node.line_end or len(lines)
        source = "\n".join(lines[max(start - 1, 0) : end])
    return {
        "node_id": node.id,
        "qualname": node.qualname,
        "file": node.file,
        "line_start": node.line_start,
        "line_end": node.line_end,
        "code": source,
        "incoming": incoming,
        "outgoing": outgoing,
    }

"""Project selection and graph API for the CodeFlow viewer."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from codeflow.graph.builder import GraphBuilder
from codeflow.runtime.recorder import load_recorded_graph
from codeflow.schema import Graph, GraphNode
from codeflow.tracing.scenario import trace_scenario
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from codeflow_server.projects import ProjectHandle, open_project


class OpenProjectRequest(BaseModel):
    location: str = Field(min_length=1, max_length=4096)


def create_app(project: str | Path, web_dist: Path | None = None) -> FastAPI:
    """Analyze ``project`` once and serve its graph, routes, scenarios and source."""
    project_handle = open_project(project)
    try:
        graph = _analyze_project(project_handle)
    except Exception:
        project_handle.close()
        raise

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            active: ProjectHandle = application.state.project_handle
            active.close()

    app = FastAPI(title="CodeFlow API", version="0.1.0", lifespan=lifespan)
    app.state.project_handle = project_handle
    app.state.graph = graph

    @app.get("/api/project")
    def get_project() -> dict[str, str | int]:
        active: ProjectHandle = app.state.project_handle
        return {
            "location": active.location,
            "name": active.name,
            "routes": len(app.state.graph.routes),
        }

    @app.post("/api/project")
    def set_project(request: OpenProjectRequest) -> dict[str, str | int]:
        try:
            next_handle = open_project(request.location)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            next_graph = _analyze_project(next_handle)
        except (OSError, ValueError, RuntimeError) as exc:
            next_handle.close()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        previous_handle: ProjectHandle = app.state.project_handle
        app.state.project_handle = next_handle
        app.state.graph = next_graph
        previous_handle.close()
        return {
            "location": next_handle.location,
            "name": next_handle.name,
            "routes": len(next_graph.routes),
        }

    @app.get("/api/graph")
    def get_graph() -> Graph:
        return app.state.graph

    @app.get("/api/routes")
    def get_routes() -> list[dict[str, str]]:
        return [route.model_dump() for route in app.state.graph.routes]

    @app.get("/api/scenario")
    def get_scenario(
        route: str = Query(..., description='Route selector, e.g. "POST /orders".'),
        max_depth: int = Query(default=8, ge=0, le=64),
        mode: Literal["static", "observed", "both"] = Query(default="static"),
    ) -> dict[str, Any]:
        try:
            graph: Graph = app.state.graph
            scenario = trace_scenario(graph, route, max_depth=max_depth)
            observed_routes = graph.meta.get("observed_routes", {})
            observations = observed_routes.get(scenario.route.id, [])
            payload = scenario.model_dump(mode="json")
            payload["mode"] = mode
            payload["has_observation"] = bool(observations)
            observed_steps = [
                {
                    "node_id": observed["node_id"],
                    "edge_id": observed.get("edge_id"),
                    "depth": order + 1,
                    "order": order + 1,
                    "observation": {
                        "args": observed.get("args", {}),
                        "return": observed.get("return"),
                    },
                }
                for order, observed in enumerate(observations)
            ]
            payload["observed_steps"] = observed_steps
            if mode == "observed" and observations:
                runtime_steps: list[dict[str, Any]] = [
                    {
                        "node_id": scenario.route.id,
                        "edge_id": None,
                        "depth": 0,
                        "order": 0,
                        "observation": None,
                    }
                ]
                runtime_steps.extend(observed_steps)
                payload["steps"] = runtime_steps
            return payload
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/source")
    def get_source(node_id: str = Query(...)) -> dict[str, Any]:
        graph: Graph = app.state.graph
        node = next((candidate for candidate in graph.nodes if candidate.id == node_id), None)
        if node is None:
            raise HTTPException(status_code=404, detail="Node not found")
        project_handle: ProjectHandle = app.state.project_handle
        return _source_payload(project_handle.root, graph, node)

    distribution = (web_dist or Path(__file__).parents[3] / "web" / "dist").resolve()
    if distribution.is_dir():
        app.mount("/", StaticFiles(directory=distribution, html=True), name="viewer")
    return app


def _analyze_project(project: ProjectHandle) -> Graph:
    return load_recorded_graph(project.root, GraphBuilder().analyze(project.root))


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

"""Canonical graph and scenario schema shared with the TypeScript viewer."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

NodeKind = Literal["module", "function", "class", "route", "model", "table", "external"]
EdgeType = Literal["calls", "imports", "reads", "writes", "depends_on", "http_call"]
ConfidenceKind = Literal["parsed", "observed", "inferred"]


class GraphNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: NodeKind
    name: str
    qualname: str
    file: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str
    target: str
    type: EdgeType
    kind: ConfidenceKind
    meta: dict[str, Any] = Field(default_factory=dict)


class Route(BaseModel):
    id: str
    method: str
    path: str
    handler_node_id: str


class Graph(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    routes: list[Route] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class ScenarioStep(BaseModel):
    node_id: str
    edge_id: str | None = None
    depth: int
    order: int


class Scenario(BaseModel):
    route: Route
    steps: list[ScenarioStep]
    tables_read: list[str] = Field(default_factory=list)
    tables_written: list[str] = Field(default_factory=list)

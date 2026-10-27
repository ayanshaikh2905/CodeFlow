"""Helpers shared by static detectors."""

import ast
import hashlib
from typing import Any

from codeflow.schema import Edge, GraphNode


def stable_edge_id(edge_type: str, source: str, target: str) -> str:
    token = hashlib.sha1(f"{edge_type}|{source}|{target}".encode()).hexdigest()[:12]
    return f"{edge_type}:{token}"


def make_edge(
    source: str,
    target: str,
    edge_type: str,
    kind: str = "parsed",
    meta: dict[str, Any] | None = None,
) -> Edge:
    return Edge(
        id=stable_edge_id(edge_type, source, target),
        source=source,
        target=target,
        type=edge_type,  # type: ignore[arg-type]
        kind=kind,  # type: ignore[arg-type]
        meta=meta or {},
    )


def literal_string(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def node_name(node: GraphNode) -> str:
    return node.name

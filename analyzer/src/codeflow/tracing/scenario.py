"""Cycle-safe breadth-first route scenario traversal."""

from collections import deque

from codeflow.schema import Edge, Graph, Route, Scenario, ScenarioStep


def find_route(graph: Graph, selector: str) -> Route:
    """Find a route using ``METHOD /path`` or an unambiguous path selector."""
    normalized = selector.strip()
    if " " in normalized:
        method, path = normalized.split(None, 1)
        matches = [
            route
            for route in graph.routes
            if route.method == method.upper() and route.path == path.strip()
        ]
    else:
        matches = [route for route in graph.routes if route.path == normalized]
    if not matches:
        raise ValueError(f"No route matched {selector!r}")
    if len(matches) > 1:
        raise ValueError(f"Route path {selector!r} is ambiguous; include the HTTP method")
    return matches[0]


def trace_scenario(graph: Graph, selector: str, max_depth: int = 8) -> Scenario:
    """Walk calls and dependencies from one route, preserving breadth-first order."""
    if max_depth < 0:
        raise ValueError("max_depth must be zero or greater")
    route = find_route(graph, selector)
    outgoing: dict[str, list[Edge]] = {}
    for edge in graph.edges:
        if edge.type in {"calls", "depends_on"}:
            outgoing.setdefault(edge.source, []).append(edge)

    steps = [ScenarioStep(node_id=route.id, edge_id=None, depth=0, order=0)]
    queue = deque([(route.handler_node_id, route.id, 1)])
    visited = {route.id}
    while queue:
        node_id, predecessor_id, depth = queue.popleft()
        if node_id in visited or depth > max_depth:
            continue
        connecting_edge = next(
            (edge for edge in outgoing.get(predecessor_id, []) if edge.target == node_id),
            None,
        )
        # The route-to-handler edge is a calls edge and is indexed above.
        if connecting_edge is None:
            connecting_edge = next(
                (
                    edge
                    for edge in graph.edges
                    if edge.source == predecessor_id
                    and edge.target == node_id
                    and edge.type == "calls"
                ),
                None,
            )
        visited.add(node_id)
        steps.append(
            ScenarioStep(
                node_id=node_id,
                edge_id=connecting_edge.id if connecting_edge else None,
                depth=depth,
                order=len(steps),
            )
        )
        if depth >= max_depth:
            continue
        for edge in outgoing.get(node_id, []):
            if edge.target not in visited:
                queue.append((edge.target, node_id, depth + 1))

    traversed = {step.node_id for step in steps}
    model_tables: dict[str, str] = graph.meta.get("model_tables", {})
    table_reads: set[str] = set()
    table_writes: set[str] = set()
    for edge in graph.edges:
        if edge.source not in traversed or edge.type not in {"reads", "writes"}:
            continue
        table_name = model_tables.get(edge.target)
        if table_name:
            (table_reads if edge.type == "reads" else table_writes).add(table_name)
    return Scenario(
        route=route,
        steps=steps,
        tables_read=sorted(table_reads),
        tables_written=sorted(table_writes),
    )

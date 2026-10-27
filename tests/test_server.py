"""Read-only graph API behavior for the demo app."""

from pathlib import Path

from codeflow_server.app import create_app
from fastapi.testclient import TestClient


def test_server_graph_routes_scenario_and_source() -> None:
    project = Path(__file__).parents[1] / "demo_app"
    client = TestClient(create_app(project))

    graph = client.get("/api/graph")
    assert graph.status_code == 200
    assert len(graph.json()["nodes"]) > 0

    routes = client.get("/api/routes").json()
    order_route = next(
        route
        for route in routes
        if route["method"] == "POST" and route["path"] == "/orders"
    )
    scenario = client.get("/api/scenario", params={"route": "POST /orders"})
    assert scenario.status_code == 200
    assert scenario.json()["route"]["id"] == order_route["id"]
    assert set(scenario.json()["tables_read"]) == {"products", "users"}

    source = client.get(
        "/api/source", params={"node_id": order_route["handler_node_id"]}
    )
    assert source.status_code == 200
    assert "def post_order" in source.json()["code"]
    assert source.json()["incoming"]


def test_server_rejects_unknown_route_and_node() -> None:
    project = Path(__file__).parents[1] / "demo_app"
    client = TestClient(create_app(project))
    assert (
        client.get("/api/scenario", params={"route": "DELETE /missing"}).status_code
        == 404
    )
    assert client.get("/api/source", params={"node_id": "missing"}).status_code == 404

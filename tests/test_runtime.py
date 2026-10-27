"""Runtime overlay tests covering observed edges, ghost calls and redaction."""

import json
from pathlib import Path

import codeflow_server.app as server_module
import demo_app.database as demo_database
import pytest
from codeflow.graph.builder import GraphBuilder
from codeflow.runtime.recorder import (
    _load_app,
    _route_path,
    _value_summary,
    merge_recorded_graph,
    record_request,
)
from codeflow.schema import Graph
from demo_app.models import Base, Product, User
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def test_record_demo_request_adds_observed_decorator_ghost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = Path(__file__).parents[1] / "demo_app"
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        session.add_all(
            [
                User(id=1, email="test@example.test", name="Test User"),
                Product(id=1, name="Test Product", price=5.0),
            ]
        )
        session.commit()
    app = _load_app(project)

    def session_override():
        with factory() as session:
            yield session

    monkeypatch.setitem(
        app.dependency_overrides, demo_database.get_session, session_override
    )
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(
        json.dumps({"items": [{"product_id": 1, "quantity": 1}]}), encoding="utf-8"
    )
    graph = record_request(
        project,
        "POST /orders",
        payload_path,
        GraphBuilder().analyze(project),
        headers={"X-User-ID": "1"},
    )
    assert graph.meta["recording"]["status_code"] == 201
    assert graph.meta["recording"]["recorded_steps"] > 0
    observed_route = graph.meta["observed_routes"][
        next(route.id for route in graph.routes if route.path == "/orders")
    ]
    assert observed_route
    assert any(
        edge.meta.get("ghost") and edge.meta.get("mechanism") == "decorator"
        for edge in graph.edges
    )
    assert any(edge.meta.get("observed") for edge in graph.edges)
    assert any(step["return"] is not None for step in observed_route)
    engine.dispose()

    def merge_overlay(root: Path, static_graph: Graph) -> Graph:
        del root
        return merge_recorded_graph(static_graph, graph)

    monkeypatch.setattr(server_module, "load_recorded_graph", merge_overlay)
    client = TestClient(server_module.create_app(project))
    response = client.get(
        "/api/scenario", params={"route": "POST /orders", "mode": "observed"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["has_observation"] is True
    assert payload["mode"] == "observed"
    assert any(step.get("observation") for step in payload["steps"])


def test_runtime_value_summaries_redact_sensitive_keys_and_truncate() -> None:
    sensitive = _value_summary(
        {"password": "dont-show", "nested": {"access_token": "hide-this", "ok": 7}},
        "payload",
    )
    assert "dont-show" not in sensitive["repr"]
    assert "hide-this" not in sensitive["repr"]
    direct = _value_summary("secret-value", "authorization")
    assert direct["repr"] == "<redacted>"
    long_value = _value_summary("x" * 500, "value")
    assert len(long_value["repr"]) <= 200


def test_runtime_path_parameters_must_be_explicit() -> None:
    with pytest.raises(ValueError, match="Provide values for route path parameter"):
        _route_path("/orders/{order_id}", {})
    assert _route_path("/orders/{order_id}", {"order_id": "a/b"}) == "/orders/a%2Fb"
    with pytest.raises(ValueError, match="Unknown route path parameter"):
        _route_path("/orders", {"order_id": "1"})

"""Runtime overlay tests covering observed edges, ghost calls and redaction."""

import json
from pathlib import Path

import codeflow_server.app as server_module
import pytest
from codeflow.graph.builder import GraphBuilder
from codeflow.runtime.recorder import (
    _value_summary,
    merge_recorded_graph,
    record_request,
)
from codeflow.schema import Graph
from fastapi.testclient import TestClient


def test_record_demo_request_adds_observed_decorator_ghost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = Path(__file__).parents[1] / "demo_app"
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(
        json.dumps({"items": [{"product_id": 1, "quantity": 1}]}), encoding="utf-8"
    )
    graph = record_request(
        project,
        "POST /orders",
        payload_path,
        GraphBuilder().analyze(project),
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

"""Capture one FastAPI request and merge runtime evidence into its graph."""

from __future__ import annotations

import ast
import importlib
import json
import os
import re
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import FrameType
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.testclient import TestClient

from codeflow.detectors.common import make_edge
from codeflow.graph.builder import GraphBuilder
from codeflow.parsing.python import DEFAULT_EXCLUDED
from codeflow.schema import Edge, Graph, GraphNode, Route

SECRET_KEY = re.compile(r"password|token|secret|authorization", re.IGNORECASE)
MAX_REPR_LENGTH = 200


def record_request(
    project: Path,
    selector: str,
    payload_path: Path | None,
    graph: Graph | None = None,
    *,
    path_params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> Graph:
    """Invoke one API route with ``TestClient`` and attach observed evidence."""
    root = project.resolve()
    result = graph or GraphBuilder().analyze(root)
    route = _find_route(result, selector)
    body = _read_payload(payload_path)
    app = _load_app(root)
    runtime_path = _route_path(route.path, path_params or {})
    recorder = _ObservedRecorder(root, result, route)
    monitoring_scope = _monitoring_context(recorder)
    if monitoring_scope is not None:
        with monitoring_scope, TestClient(app) as client:
            response = client.request(
                route.method,
                runtime_path,
                headers=headers,
                **({"json": body} if body is not None else {}),
            )
    else:
        previous_thread_profile = threading.getprofile()
        threading.setprofile(recorder.profile_callback)
        try:
            with TestClient(_profiled_app(app, recorder)) as client:
                response = client.request(
                    route.method,
                    runtime_path,
                    headers=headers,
                    **({"json": body} if body is not None else {}),
                )
        finally:
            threading.setprofile(previous_thread_profile)

    recorder.finalize()
    result.meta["recording"] = {
        "route_id": route.id,
        "status_code": response.status_code,
        "python_version": sys.version.split()[0],
        "monitor": recorder.monitor_name,
        "recorded_steps": len(recorder.steps),
    }
    if recorder.errors:
        result.meta.setdefault("warnings", []).extend(recorder.errors)
    result.meta.setdefault("observed_routes", {})[route.id] = recorder.steps
    if response.status_code >= 400:
        result.meta.setdefault("warnings", []).append(
            f"Recorded request returned HTTP {response.status_code}: "
            f"{response.text[:MAX_REPR_LENGTH]}"
        )
    return result


def merge_recorded_graph(static_graph: Graph, recorded_graph: Graph) -> Graph:
    """Copy observed nodes, edges and step values into a freshly parsed graph."""
    nodes = {node.id: node for node in static_graph.nodes}
    for node in recorded_graph.nodes:
        if node.id not in nodes:
            nodes[node.id] = node
    edges = {edge.id: edge for edge in static_graph.edges}
    for observed_edge in recorded_graph.edges:
        existing = edges.get(observed_edge.id)
        if existing is None:
            edges[observed_edge.id] = observed_edge
        elif observed_edge.meta.get("observed"):
            existing.meta.update(observed_edge.meta)
    static_graph.nodes = list(nodes.values())
    static_graph.edges = list(edges.values())
    for key in ("observed_routes", "recording"):
        if key in recorded_graph.meta:
            static_graph.meta[key] = recorded_graph.meta[key]
    if recorded_graph.meta.get("warnings"):
        static_graph.meta.setdefault("warnings", []).extend(recorded_graph.meta["warnings"])
    return static_graph


def load_recorded_graph(project: Path, graph: Graph) -> Graph:
    """Merge the default adjacent observation file when it exists."""
    recording_path = project.resolve() / "graph.observed.json"
    if not recording_path.is_file():
        return graph
    try:
        recorded = Graph.model_validate_json(recording_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        graph.meta.setdefault("warnings", []).append(f"Could not load {recording_path.name}: {exc}")
        return graph
    return merge_recorded_graph(graph, recorded)


class _ObservedRecorder:
    """Collect project-local frame transitions and argument/return summaries."""

    def __init__(self, root: Path, graph: Graph, route: Route) -> None:
        self.root = root.resolve()
        self._root_prefix = os.path.normcase(str(self.root).rstrip(os.sep) + os.sep)
        self.graph = graph
        self.route = route
        self.nodes = {node.id: node for node in graph.nodes}
        self.edges = {edge.id: edge for edge in graph.edges}
        self.steps: list[dict[str, Any]] = []
        self.frame_steps: dict[int, int] = {}
        self._busy = threading.local()
        self.errors: list[str] = []
        self.monitor_name = "sys.setprofile"

    def profile_callback(self, frame: FrameType, event: str, arg: object) -> None:
        if self._relative_file(frame.f_code.co_filename) is None:
            return
        if getattr(self._busy, "value", False):
            return
        self._busy.value = True
        try:
            if event == "call":
                self._on_call(frame)
            elif event == "return":
                self._on_return(frame, arg)
        except Exception as exc:
            self.errors.append(f"Runtime capture skipped a frame: {exc}")
        finally:
            self._busy.value = False

    def _on_call(self, frame: FrameType) -> None:
        node = self._node_for_frame(frame)
        if node is None:
            return
        frame_id = id(frame)
        parent = self._nearest_project_frame(frame.f_back)
        parent_node = self._node_for_frame(parent) if parent else None
        args = _capture_arguments(frame)
        step: dict[str, Any] = {
            "node_id": node.id,
            "order": len(self.steps),
            "args": args,
            "return": None,
        }
        self.steps.append(step)
        self.frame_steps[frame_id] = len(self.steps) - 1

        if parent_node is not None:
            mechanism = _guess_mechanism(parent_node, node, self.edges, self.nodes)
            edge = self._observe_edge(parent_node.id, node.id, "calls", mechanism)
            step["edge_id"] = edge.id
        elif node.id == self.route.handler_node_id:
            edge = self._observe_edge(self.route.id, node.id, "calls", "route dispatch")
            step["edge_id"] = edge.id
        else:
            dependency_edges = [
                edge
                for edge in self.edges.values()
                if edge.type == "depends_on" and edge.target == node.id
            ]
            if dependency_edges:
                for edge in dependency_edges:
                    edge.meta["observed"] = True
                    edge.meta["observed_kind"] = "observed"
                step["edge_id"] = dependency_edges[0].id
            elif self.steps:
                edge = self._observe_edge(
                    self.route.handler_node_id,
                    node.id,
                    "calls",
                    "framework callback",
                )
                step["edge_id"] = edge.id

    def _on_return(self, frame: FrameType, value: object) -> None:
        step_index = self.frame_steps.pop(id(frame), None)
        if step_index is None:
            return
        node = self.nodes.get(self.steps[step_index]["node_id"])
        return_name = node.name if node else "return"
        self.steps[step_index]["return"] = _value_summary(value, return_name)

    def _node_for_frame(self, frame: FrameType | None) -> GraphNode | None:
        if frame is None or frame.f_code.co_name == "<module>":
            return None
        relative = self._relative_file(frame.f_code.co_filename)
        if relative is None:
            return None
        module_name = _module_name(Path(relative))
        qualname = f"{module_name}.{frame.f_code.co_qualname}"
        existing = next(
            (
                candidate
                for candidate in self.nodes.values()
                if candidate.file == relative and candidate.qualname == qualname
            ),
            None,
        )
        if existing:
            return existing
        node_id = f"{relative}::{qualname}"
        node = GraphNode(
            id=node_id,
            kind="function",
            name=frame.f_code.co_name,
            qualname=qualname,
            file=relative,
            line_start=frame.f_code.co_firstlineno,
            meta={"runtime_only": True},
        )
        self.nodes[node.id] = node
        self.graph.nodes.append(node)
        return node

    def _relative_file(self, filename: str) -> str | None:
        if not os.path.normcase(filename).startswith(self._root_prefix):
            return None
        try:
            return Path(filename).relative_to(self.root).as_posix()
        except ValueError:
            return None

    def _nearest_project_frame(self, frame: FrameType | None) -> FrameType | None:
        while frame:
            if self._node_for_frame(frame):
                return frame
            frame = frame.f_back
        return None

    def _observe_edge(self, source: str, target: str, edge_type: str, mechanism: str) -> Edge:
        candidate = make_edge(source, target, edge_type)
        existing = self.edges.get(candidate.id)
        if existing:
            existing.meta["observed"] = True
            existing.meta["observed_kind"] = "observed"
            existing.meta.setdefault("static_kind", existing.kind)
            if mechanism:
                existing.meta.setdefault("mechanism", mechanism)
            return existing
        observed = make_edge(
            source,
            target,
            edge_type,
            kind="observed",
            meta={"ghost": True, "mechanism": mechanism},
        )
        self.edges[observed.id] = observed
        self.graph.edges.append(observed)
        return observed

    def finalize(self) -> None:
        self.graph.nodes = list(self.nodes.values())
        self.graph.edges = list(self.edges.values())


def _find_route(graph: Graph, selector: str) -> Route:
    from codeflow.tracing.scenario import find_route

    return find_route(graph, selector)


def _read_payload(payload_path: Path | None) -> Any:
    if payload_path is None:
        return None
    try:
        return json.loads(payload_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read JSON payload {payload_path}: {exc}") from exc


def _load_app(project: Path) -> FastAPI:
    candidates: list[tuple[int, str]] = []
    for source_path in sorted(project.rglob("*.py")):
        relative = source_path.relative_to(project)
        if any(part in DEFAULT_EXCLUDED for part in relative.parts):
            continue
        try:
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        if not _looks_like_fastapi_app(tree):
            continue
        module_name = _module_name(relative)
        priority = 0 if relative.stem in {"main", "app"} else 1
        candidates.append((priority, module_name))

    if not candidates:
        raise ValueError(f"No likely FastAPI app module was found under {project}")
    search_paths = [project, project.parent]
    inserted: list[str] = []
    for search_path in reversed(search_paths):
        value = str(search_path)
        if value not in sys.path:
            sys.path.insert(0, value)
            inserted.append(value)
    try:
        for _, module_name in sorted(set(candidates)):
            try:
                module = importlib.import_module(module_name)
            except Exception:
                continue
            app = next(
                (
                    app_value
                    for name in ("app", "application")
                    if isinstance((app_value := getattr(module, name, None)), FastAPI)
                ),
                None,
            )
            if app is not None:
                return app
            factory = getattr(module, "create_app", None)
            if callable(factory):
                try:
                    created = factory()
                except TypeError:
                    continue
                if isinstance(created, FastAPI):
                    return created
    finally:
        for value in inserted:
            try:
                sys.path.remove(value)
            except ValueError:
                pass
    raise ValueError(f"No FastAPI instance was imported from {project}")


def _looks_like_fastapi_app(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = (
            function.id
            if isinstance(function, ast.Name)
            else function.attr
            if isinstance(function, ast.Attribute)
            else ""
        )
        if name == "FastAPI":
            return True
    return False


def _route_path(path: str, values: dict[str, str]) -> str:
    """Substitute only caller-supplied values into a templated route path."""
    parameter_names = re.findall(r"\{([^{}:]+)(?::[^{}]+)?\}", path)
    missing = sorted(set(parameter_names) - values.keys())
    unused = sorted(values.keys() - set(parameter_names))
    if missing:
        names = ", ".join(missing)
        raise ValueError(f"Provide values for route path parameter(s): {names}.")
    if unused:
        names = ", ".join(unused)
        raise ValueError(f"Unknown route path parameter(s): {names}.")
    if any(not values[name] for name in parameter_names):
        raise ValueError("Route path parameter values cannot be empty.")
    return re.sub(
        r"\{([^{}:]+)(?::[^{}]+)?\}",
        lambda match: quote(values[match.group(1)], safe=""),
        path,
    )


def _module_name(relative: Path) -> str:
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _profiled_app(app: FastAPI, recorder: _ObservedRecorder) -> Callable[..., Any]:
    """Install the fallback profiler inside TestClient's ASGI execution thread."""

    async def profiled_asgi(scope: Any, receive: Any, send: Any) -> None:
        previous = sys.getprofile()
        sys.setprofile(recorder.profile_callback)
        try:
            await app(scope, receive, send)
        finally:
            sys.setprofile(previous)

    return profiled_asgi


def _capture_arguments(frame: FrameType) -> dict[str, dict[str, str]]:
    code = frame.f_code
    argument_count = code.co_argcount + code.co_kwonlyargcount
    if code.co_flags & 0x04:
        argument_count += 1
    if code.co_flags & 0x08:
        argument_count += 1
    captured: dict[str, dict[str, str]] = {}
    for name in code.co_varnames[:argument_count]:
        if name in frame.f_locals:
            captured[name] = _value_summary(frame.f_locals[name], name)
    return captured


def _value_summary(value: object, name: str) -> dict[str, str]:
    if SECRET_KEY.search(name):
        return {"type": type(value).__name__, "repr": "<redacted>"}
    try:
        safe_value = _safe_value(value, set())
        rendered = repr(safe_value)
    except Exception:
        rendered = f"<{type(value).__name__}>"
    if len(rendered) > MAX_REPR_LENGTH:
        rendered = f"{rendered[: MAX_REPR_LENGTH - 1]}…"
    return {"type": type(value).__name__, "repr": rendered}


def _safe_value(value: object, visited: set[int]) -> object:
    if id(value) in visited:
        return "<recursive>"
    if isinstance(value, dict):
        visited.add(id(value))
        safe = {
            str(key): "<redacted>" if SECRET_KEY.search(str(key)) else _safe_value(item, visited)
            for key, item in value.items()
        }
        visited.remove(id(value))
        return safe
    if isinstance(value, (list, tuple, set, frozenset)):
        visited.add(id(value))
        converted = [_safe_value(item, visited) for item in value]
        visited.remove(id(value))
        return type(value)(converted) if isinstance(value, (tuple, list)) else converted
    if isinstance(value, (str, bytes, int, float, bool, type(None))):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _safe_value(model_dump(), visited)
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, dict):
        public = {key: item for key, item in attributes.items() if not key.startswith("_")}
        if public:
            return _safe_value(public, visited)
    return repr(value)


def _guess_mechanism(
    caller: GraphNode,
    callee: GraphNode,
    edges: dict[str, Edge],
    nodes: dict[str, GraphNode],
) -> str:
    if "<locals>.wrapper" in callee.qualname or callee.name == "wrapper":
        return "decorator"
    if "<locals>.wrapper" in caller.qualname or caller.name == "wrapper":
        return "decorator"
    if callee.name == "<lambda>":
        return "framework callback"
    for edge in edges.values():
        if edge.source != caller.id or edge.type != "calls" or edge.kind != "inferred":
            continue
        target = nodes.get(edge.target)
        if target and target.meta.get("unresolved"):
            return "dynamic dispatch"
    return "runtime dispatch"


def _monitoring_context(recorder: _ObservedRecorder) -> Any:
    """Use Python 3.12's low-overhead sys.monitoring events when available."""
    monitoring_candidate = getattr(sys, "monitoring", None)
    if monitoring_candidate is None:
        return None
    monitoring: Any = monitoring_candidate
    tool_id: int | None = None
    for candidate in (4, 3, 5, 2, 1, 0):
        try:
            if monitoring.get_tool(candidate) is None:
                monitoring.use_tool_id(candidate, "CodeFlow")
                tool_id = candidate
                break
        except (ValueError, RuntimeError):
            continue
    if tool_id is None:
        return None

    events = monitoring.events
    selected_events = [events.PY_START, events.PY_RESUME, events.PY_RETURN, events.PY_YIELD]
    if hasattr(events, "PY_UNWIND"):
        selected_events.append(events.PY_UNWIND)

    def find_frame(code: Any) -> FrameType | None:
        frame: FrameType | None = sys._getframe()
        while frame:
            if frame.f_code is code:
                return frame
            frame = frame.f_back
        return None

    def on_start(code: Any, instruction_offset: int) -> None:
        del instruction_offset
        if getattr(recorder._busy, "value", False):
            return
        recorder._busy.value = True
        try:
            frame = find_frame(code)
            if frame is not None:
                recorder._on_call(frame)
        finally:
            recorder._busy.value = False

    def on_return(code: Any, instruction_offset: int, value: object) -> None:
        del instruction_offset
        if getattr(recorder._busy, "value", False):
            return
        recorder._busy.value = True
        try:
            frame = find_frame(code)
            if frame is not None:
                recorder._on_return(frame, value)
        finally:
            recorder._busy.value = False

    def on_unwind(code: Any, instruction_offset: int, exception: BaseException) -> None:
        del instruction_offset, exception
        if getattr(recorder._busy, "value", False):
            return
        recorder._busy.value = True
        try:
            frame = find_frame(code)
            if frame is not None:
                recorder._on_return(frame, None)
        finally:
            recorder._busy.value = False

    callbacks: dict[int, Callable[..., object]] = {
        events.PY_START: on_start,
        events.PY_RESUME: on_start,
        events.PY_RETURN: on_return,
        events.PY_YIELD: on_return,
    }
    if hasattr(events, "PY_UNWIND"):
        callbacks[events.PY_UNWIND] = on_unwind
    try:
        for event, callback in callbacks.items():
            monitoring.register_callback(tool_id, event, callback)
        monitoring.set_events(tool_id, sum(set(selected_events)))
    except Exception:
        try:
            monitoring.free_tool_id(tool_id)
        except Exception:
            pass
        return None
    recorder.monitor_name = "sys.monitoring"

    class MonitoringScope:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *exc_info: object) -> None:
            try:
                monitoring.set_events(tool_id, events.NO_EVENTS)
                for event in callbacks:
                    monitoring.register_callback(tool_id, event, None)
                monitoring.free_tool_id(tool_id)
            except Exception:
                pass

    return MonitoringScope()

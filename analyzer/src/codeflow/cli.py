"""Command-line entry point for CodeFlow."""

import importlib
import json
from pathlib import Path
from typing import Annotated

import typer

from codeflow.graph.builder import GraphBuilder
from codeflow.tracing.scenario import trace_scenario

app = typer.Typer(no_args_is_help=True, help="Analyze Python backend request flows.")


@app.command()
def analyze(
    project: Annotated[Path, typer.Argument(exists=True, file_okay=False, readable=True)],
    output: Annotated[Path, typer.Option("-o", "--output", help="Write graph JSON here.")],
) -> None:
    """Analyze a Python project and save its graph JSON."""
    graph = GraphBuilder().analyze(project)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(graph.model_dump_json(indent=2), encoding="utf-8")
    typer.echo(f"Wrote {len(graph.nodes)} nodes and {len(graph.edges)} edges to {output}.")
    for warning in graph.meta.get("warnings", []):
        typer.echo(f"Warning: {warning}", err=True)


@app.command()
def trace(
    project: Annotated[Path, typer.Argument(exists=True, file_okay=False, readable=True)],
    route: Annotated[str, typer.Option("--route", help='Route selector, e.g. "POST /orders".')],
    output: Annotated[
        Path | None,
        typer.Option("-o", "--output", help="Optionally write scenario JSON to this file."),
    ] = None,
    max_depth: Annotated[
        int, typer.Option("--max-depth", min=0, help="Maximum traversal depth.")
    ] = 8,
) -> None:
    """Trace one route through parsed calls and dependencies."""
    graph = GraphBuilder().analyze(project)
    scenario = trace_scenario(graph, route, max_depth=max_depth)
    payload = scenario.model_dump_json(indent=2)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    typer.echo(
        f"{scenario.route.method} {scenario.route.path}: {len(scenario.steps)} steps, "
        f"reads {', '.join(scenario.tables_read) or 'no tables'}, "
        f"writes {', '.join(scenario.tables_written) or 'no tables'}."
    )
    if output is None:
        typer.echo(json.dumps(scenario.model_dump(mode="json"), indent=2))
    else:
        typer.echo(f"Wrote scenario JSON to {output}.")


@app.command()
def serve(
    project: Annotated[
        str,
        typer.Argument(help="A local project folder or github.com repository URL."),
    ],
    host: Annotated[str, typer.Option(help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535, help="HTTP port.")] = 8000,
) -> None:
    """Serve graph APIs and the built viewer for a Python project."""
    try:
        import uvicorn

        server_app = importlib.import_module("codeflow_server.app")
    except ImportError as exc:
        raise typer.BadParameter(
            "Install the server package with `pip install -e server`."
        ) from exc
    try:
        application = server_app.create_app(project)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    uvicorn.run(application, host=host, port=port)


@app.command()
def record(
    project: Annotated[Path, typer.Argument(exists=True, file_okay=False, readable=True)],
    route: Annotated[str, typer.Option("--route", help='Route selector, e.g. "POST /orders".')],
    payload: Annotated[
        Path | None, typer.Option("--payload", help="Optional JSON request body file.")
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option("-o", "--output", help="Write observed graph JSON."),
    ] = None,
    path_params_json: Annotated[
        str | None,
        typer.Option("--path-params", help='JSON object for route values, e.g. {"order_id":"42"}.'),
    ] = None,
    header_values: Annotated[
        list[str] | None,
        typer.Option("--header", help="Request header as 'Name: Value'; repeat for more."),
    ] = None,
) -> None:
    """Run one FastAPI route and merge observed calls into its graph."""
    runtime_module = importlib.import_module("codeflow.runtime.recorder")
    graph = GraphBuilder().analyze(project)
    path_params: dict[str, str] = {}
    if path_params_json:
        try:
            parsed_params = json.loads(path_params_json)
        except json.JSONDecodeError as exc:
            raise typer.BadParameter("--path-params must be a JSON object.") from exc
        if not isinstance(parsed_params, dict) or not all(
            isinstance(key, str) and isinstance(value, (str, int, float))
            for key, value in parsed_params.items()
        ):
            raise typer.BadParameter("--path-params must map names to string or numeric values.")
        path_params = {key: str(value) for key, value in parsed_params.items()}
    headers: dict[str, str] = {}
    for header in header_values or []:
        name, separator, value = header.partition(":")
        if not separator or not name.strip():
            raise typer.BadParameter("Each --header must use the format 'Name: Value'.")
        normalized_name = name.strip()
        if any(existing.lower() == normalized_name.lower() for existing in headers):
            raise typer.BadParameter(f"Header {normalized_name!r} was specified more than once.")
        headers[normalized_name] = value.strip()
    try:
        recorded = runtime_module.record_request(
            project,
            route,
            payload,
            graph,
            path_params=path_params,
            headers=headers,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    output_path = output or project / "graph.observed.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(recorded.model_dump_json(indent=2), encoding="utf-8")
    recording = recorded.meta.get("recording", {})
    typer.echo(
        f"Recorded {recording.get('recorded_steps', 0)} runtime steps "
        f"with {recording.get('monitor', 'unknown')} and wrote {output_path}."
    )


if __name__ == "__main__":
    app()

"""Command-line entry point for CodeFlow."""

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


if __name__ == "__main__":
    app()

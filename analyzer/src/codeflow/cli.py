"""Command-line entry point for CodeFlow."""

from pathlib import Path
from typing import Annotated

import typer

from codeflow.graph.builder import GraphBuilder

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


if __name__ == "__main__":
    app()

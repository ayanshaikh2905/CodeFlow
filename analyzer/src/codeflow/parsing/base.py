"""Parser interface for future language frontends."""

from pathlib import Path
from typing import Protocol

from codeflow.parsing.python import ParsedModule


class Parser(Protocol):
    """Interface implemented by language-specific project parsers."""

    def parse(self, root: Path, excluded: set[str]) -> list[ParsedModule]: ...

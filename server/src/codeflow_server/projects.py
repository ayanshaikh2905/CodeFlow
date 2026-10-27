"""Local project and GitHub repository resolution."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

_GITHUB_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+$")
_WINDOWS_RESERVED_NAME = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$", re.I)
_CLONE_TIMEOUT_SECONDS = 120


@dataclass(slots=True)
class ProjectHandle:
    """Resolved source tree and its optional temporary clone lifetime."""

    root: Path
    location: str
    _temporary_directory: tempfile.TemporaryDirectory[str] | None = None

    @property
    def name(self) -> str:
        """Display the local folder or GitHub repository name."""
        parsed = urlsplit(self.location)
        if parsed.hostname == "github.com":
            return parsed.path.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        return self.root.name or self.location

    def close(self) -> None:
        """Remove an owned clone directory, if this handle created one."""
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()
            self._temporary_directory = None


def open_project(location: str | Path) -> ProjectHandle:
    """Open a local folder or materialize a GitHub repository into a temp tree."""
    value = str(location).strip()
    if not value:
        raise ValueError("Enter a local folder path or a GitHub repository URL.")
    if _is_github_url(value):
        return _clone_github_repository(value)

    root = Path(value).expanduser().resolve()
    if not root.exists():
        raise ValueError(f"Project folder does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"Project path is not a folder: {root}")
    return ProjectHandle(root=root, location=str(root))


def _is_github_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme.lower() in {"https", "http"} and parsed.hostname == "github.com"


def _clone_github_repository(location: str) -> ProjectHandle:
    parsed = urlsplit(location)
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        raise ValueError("Use an HTTPS URL from github.com.")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("GitHub repository URLs cannot include credentials or query strings.")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        raise ValueError("Use a repository URL like https://github.com/owner/repository.")
    owner, repository = parts
    repository = repository.removesuffix(".git")
    if not all(_GITHUB_REPOSITORY.fullmatch(part) for part in (owner, repository)):
        raise ValueError("The GitHub repository URL has an invalid owner or repository name.")
    if shutil.which("git") is None:
        raise ValueError("Git must be installed to open GitHub repositories.")

    temporary_directory = tempfile.TemporaryDirectory(prefix="codeflow-github-")
    temporary_root = Path(temporary_directory.name)
    checkout = temporary_root / "repository"
    source_root = temporary_root / "source"
    hooks = temporary_root / "empty-hooks"
    hooks.mkdir()
    source_root.mkdir()
    repository_url = f"https://github.com/{owner}/{repository}.git"
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    for variable in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        environment.pop(variable, None)

    try:
        completed = subprocess.run(
            [
                "git",
                "-c",
                f"core.hooksPath={hooks}",
                "clone",
                "--depth",
                "1",
                "--no-tags",
                "--no-checkout",
                "--",
                repository_url,
                str(checkout),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=_CLONE_TIMEOUT_SECONDS,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        temporary_directory.cleanup()
        raise ValueError("Cloning the GitHub repository timed out after 120 seconds.") from exc
    except OSError as exc:
        temporary_directory.cleanup()
        raise ValueError(f"Could not run Git: {exc}") from exc

    if completed.returncode != 0:
        temporary_directory.cleanup()
        detail = completed.stderr.strip().splitlines()
        reason = detail[-1] if detail else "Git clone failed."
        raise ValueError(
            f"Could not clone the GitHub repository. Check the URL, access, and network. {reason}"
        )
    try:
        _extract_git_archive(checkout, source_root, environment)
    except (OSError, RuntimeError, tarfile.TarError, subprocess.SubprocessError) as exc:
        temporary_directory.cleanup()
        raise ValueError(f"Could not read the GitHub repository source: {exc}") from exc
    return ProjectHandle(
        root=source_root.resolve(),
        location=f"https://github.com/{owner}/{repository}",
        _temporary_directory=temporary_directory,
    )


def _extract_git_archive(repository: Path, destination: Path, environment: dict[str, str]) -> None:
    """Extract regular repository files without running checkout filters or hooks."""
    process = subprocess.Popen(
        [
            "git",
            "-c",
            f"core.hooksPath={repository.parent / 'empty-hooks'}",
            "-C",
            str(repository),
            "archive",
            "--format=tar",
            "--prefix=codeflow-source/",
            "HEAD",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        raise RuntimeError("Git did not provide archive output streams.")
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|*") as archive:
            for member in archive:
                archive_path = PurePosixPath(member.name)
                relative_parts = archive_path.parts[1:]
                if not relative_parts:
                    continue
                if archive_path.is_absolute() or any(
                    part in {"", ".", ".."}
                    or any(character in part for character in ("\\", ":", "\0"))
                    or _WINDOWS_RESERVED_NAME.fullmatch(part)
                    for part in relative_parts
                ):
                    raise RuntimeError(f"Unsafe repository archive path: {member.name}")
                target = destination.joinpath(*relative_parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = archive.extractfile(member)
                    if source is None:
                        continue
                    with source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
        stderr = process.stderr.read().decode("utf-8", errors="replace")
        return_code = process.wait(timeout=_CLONE_TIMEOUT_SECONDS)
        if return_code != 0:
            reason = stderr.strip().splitlines()
            raise RuntimeError(reason[-1] if reason else "Git archive failed.")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

"""Project selection tests for local folders and GitHub repositories."""

import io
import subprocess
import tarfile
from pathlib import Path

import codeflow_server.projects as projects_module
import pytest
from codeflow_server.app import create_app
from codeflow_server.projects import open_project
from fastapi.testclient import TestClient


def test_open_project_accepts_a_local_folder(tmp_path: Path) -> None:
    project = open_project(tmp_path)
    assert project.root == tmp_path.resolve()
    assert project.location == str(tmp_path.resolve())
    project.close()


def test_open_project_rejects_missing_folder_and_non_github_url(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        open_project(tmp_path / "missing")
    with pytest.raises(ValueError, match="does not exist"):
        open_project("https://example.com/org/repository")
    with pytest.raises(ValueError, match="credentials or query strings"):
        open_project("https://user:secret@github.com/org/repository")


def test_open_github_repository_extracts_source_without_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del tmp_path
    archive_bytes = io.BytesIO()
    source = b"from fastapi import FastAPI\napp = FastAPI()\n"
    with tarfile.open(fileobj=archive_bytes, mode="w") as archive:
        info = tarfile.TarInfo("codeflow-source/src/main.py")
        info.size = len(source)
        archive.addfile(info, io.BytesIO(source))

    class FakeArchiveProcess:
        def __init__(self) -> None:
            self.stdout = io.BytesIO(archive_bytes.getvalue())
            self.stderr = io.BytesIO()
            self.return_code: int | None = None

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            self.return_code = 0
            return 0

        def poll(self) -> int | None:
            return self.return_code

        def kill(self) -> None:
            self.return_code = -9

    def fake_clone(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        Path(command[-1]).mkdir(parents=True)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(projects_module.shutil, "which", lambda _: "git")
    monkeypatch.setattr(projects_module.subprocess, "run", fake_clone)
    monkeypatch.setattr(
        projects_module.subprocess,
        "Popen",
        lambda *args, **kwargs: FakeArchiveProcess(),
    )

    project = open_project("https://github.com/example/backend.git")
    cloned_root = project.root
    assert project.location == "https://github.com/example/backend"
    assert project.name == "backend"
    assert (project.root / "src" / "main.py").read_bytes() == source
    assert not (project.root / ".git").exists()
    project.close()
    assert not cloned_root.exists()


def test_viewer_can_switch_to_another_local_project(tmp_path: Path) -> None:
    project = tmp_path / "other_backend"
    project.mkdir()
    (project / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n"
        "@app.get('/health')\ndef health():\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    demo_project = Path(__file__).parents[1] / "demo_app"

    with TestClient(create_app(demo_project)) as client:
        response = client.post("/api/project", json={"location": str(project)})
        assert response.status_code == 200
        assert response.json()["location"] == str(project.resolve())
        assert response.json()["routes"] == 1
        assert [route["path"] for route in client.get("/api/routes").json()] == [
            "/health"
        ]
        source = client.get(
            "/api/source",
            params={"node_id": client.get("/api/routes").json()[0]["handler_node_id"]},
        )
        assert "def health" in source.json()["code"]

        invalid = client.post(
            "/api/project", json={"location": str(project / "missing")}
        )
        assert invalid.status_code == 400
        assert [route["path"] for route in client.get("/api/routes").json()] == [
            "/health"
        ]


def test_github_archive_rejects_path_traversal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_bytes = io.BytesIO()
    source = b"malicious"
    with tarfile.open(fileobj=archive_bytes, mode="w") as archive:
        info = tarfile.TarInfo("codeflow-source/../outside.py")
        info.size = len(source)
        archive.addfile(info, io.BytesIO(source))

    class FakeArchiveProcess:
        def __init__(self) -> None:
            self.stdout = io.BytesIO(archive_bytes.getvalue())
            self.stderr = io.BytesIO()
            self.return_code: int | None = None

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            self.return_code = 0
            return 0

        def poll(self) -> int | None:
            return self.return_code

        def kill(self) -> None:
            self.return_code = -9

    def fake_clone(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        Path(command[-1]).mkdir(parents=True)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(projects_module.shutil, "which", lambda _: "git")
    monkeypatch.setattr(projects_module.subprocess, "run", fake_clone)
    monkeypatch.setattr(
        projects_module.subprocess,
        "Popen",
        lambda *args, **kwargs: FakeArchiveProcess(),
    )

    with pytest.raises(ValueError, match="Unsafe repository archive path"):
        open_project("https://github.com/example/unsafe")

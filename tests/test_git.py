"""Test Git operations against isolated local repositories."""

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import call, patch

import pytest
from click.testing import CliRunner
from mypy_primer.model import Project

from ecosystem_analyzer.git import get_latest_ty_commits, resolve_ty_repo
from ecosystem_analyzer.installed_project import InstalledProject
from ecosystem_analyzer.main import cli
from ecosystem_analyzer.process import run
from ecosystem_analyzer.ty import Ty


def _git(repo: Path, *args: str) -> str:
    return run("git", *args, cwd=repo).strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "commit", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.com")
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    path = tmp_path / "source repo"
    _git(tmp_path, "init", "--initial-branch=main", str(path))
    return path


def _install(
    repo: Path, *, exclude_newer: str | None = None, paths: list[str] | None = None
) -> InstalledProject:
    project = Project(
        location=repo.as_uri(), mypy_cmd=None, pyright_cmd=None, paths=paths
    )
    with patch.object(InstalledProject, "_install_dependencies"):
        return InstalledProject(project, exclude_newer=exclude_newer)


def test_resolve_linked_worktree(repo: Path, tmp_path: Path) -> None:
    sha = _commit(repo, "Initial commit")
    worktree = tmp_path / "linked worktree"
    _git(repo, "worktree", "add", "--detach", str(worktree), sha)

    assert resolve_ty_repo(worktree) == worktree
    assert Ty(repository=worktree).commit_sha == sha


def test_resolve_invalid_repository(tmp_path: Path) -> None:
    with pytest.raises(subprocess.CalledProcessError):
        resolve_ty_repo(tmp_path)


@pytest.mark.parametrize("origin_state", ["unchanged", "missing", "wrong_url"])
def test_resolve_bare_repository_refreshes_cache(
    repo: Path, tmp_path: Path, origin_state: str
) -> None:
    first = _commit(repo, "Initial commit")
    bare = tmp_path / "bare repo"
    _git(tmp_path, "clone", "--bare", str(repo), str(bare))

    cached = resolve_ty_repo(bare)
    assert cached != bare
    assert _git(cached, "rev-parse", "--is-bare-repository") == "false"
    assert _git(cached, "rev-parse", "HEAD") == first

    if origin_state == "missing":
        _git(cached, "remote", "remove", "origin")
    elif origin_state == "wrong_url":
        _git(cached, "remote", "set-url", "origin", str(tmp_path / "missing"))

    second = _commit(repo, "New commit")
    _git(bare, "fetch", str(repo), "main:main")

    assert resolve_ty_repo(bare) == cached
    assert _git(cached, "remote", "get-url", "origin") == str(bare)
    assert _git(cached, "rev-parse", "origin/main") == second


@pytest.mark.parametrize("num_commits", [1, 2, 10])
def test_latest_ty_commits_filter_and_order(repo: Path, num_commits: int) -> None:
    first = _commit(repo, "[ty] First")
    _commit(repo, "[ruff] Unrelated\n\n[ty] Only mentioned in the body")
    second = _commit(repo, "[ty] Second — details\non the next line\n\nBody")
    third = _commit(repo, "[ty] Third")
    _git(repo, "update-ref", "refs/remotes/origin/main", third)
    _commit(repo, "[ty] Local commit outside origin/main")

    expected = [
        (first, "[ty] First"),
        (second, "[ty] Second — details"),
        (third, "[ty] Third"),
    ]
    assert get_latest_ty_commits(repo, num_commits) == expected[-num_commits:]
    assert _git(repo, "rev-parse", "HEAD") == third


def test_latest_ty_commits_without_matches(repo: Path) -> None:
    sha = _commit(repo, "[ruff] Unrelated")
    _git(repo, "update-ref", "refs/remotes/origin/main", sha)
    assert get_latest_ty_commits(repo, 10) == []


def test_history_uses_commit_shas_and_messages(repo: Path, tmp_path: Path) -> None:
    first = _commit(repo, "[ty] First\n\nBody")
    second = _commit(repo, "[ty] Second")
    _git(repo, "update-ref", "refs/remotes/origin/main", second)
    output = tmp_path / "history.json"

    with (
        patch("ecosystem_analyzer.main.Manager") as manager_class,
        patch("ecosystem_analyzer.main.get_ecosystem_projects", return_value={}),
    ):
        manager = manager_class.return_value
        manager.run_for_commit.return_value = [{"diagnostics": []}]
        result = CliRunner().invoke(
            cli, ["--repository", str(repo), "history", "--output", str(output)]
        )

    assert result.exit_code == 0, result.output
    assert manager.run_for_commit.call_args_list == [call(first), call(second)]
    assert json.loads(output.read_text()) == {
        "statistics": [
            {
                "commit": first[:7],
                "commit_message": "[ty] First",
                "total_diagnostics": 0,
            },
            {
                "commit": second[:7],
                "commit_message": "[ty] Second",
                "total_diagnostics": 0,
            },
        ]
    }


def test_project_clone_and_update(repo: Path) -> None:
    first = _commit(repo, "Initial commit")
    project = _install(repo)
    assert project.current_commit == first
    assert project.default_branch == "main"
    assert (
        _git(project.root_directory, "rev-parse", "--is-shallow-repository") == "true"
    )

    second = _commit(repo, "New commit")
    updated = _install(repo)
    assert updated.root_directory == project.root_directory
    assert updated.current_commit == second


def test_project_clone_and_update_recursive_submodules(
    repo: Path, tmp_path: Path
) -> None:
    leaf = tmp_path / "leaf"
    _git(tmp_path, "init", "--initial-branch=main", str(leaf))
    (leaf / "module.py").write_text("first = 1\n")
    _git(leaf, "add", "module.py")
    _commit(leaf, "Initial leaf")

    child = tmp_path / "child"
    _git(tmp_path, "init", "--initial-branch=main", str(child))
    _git(child, "submodule", "add", leaf.as_uri(), "nested leaf")
    _commit(child, "Initial child")
    _git(repo, "submodule", "add", child.as_uri(), "nested child")
    _commit(repo, "Initial parent")

    project = _install(repo)
    cached_module = (
        project.root_directory / "nested child" / "nested leaf" / "module.py"
    )
    assert cached_module.read_text() == "first = 1\n"

    (leaf / "module.py").write_text("second = 2\n")
    _git(leaf, "add", "module.py")
    new_leaf = _commit(leaf, "Update leaf")
    _git(child / "nested leaf", "fetch", "origin")
    _git(child / "nested leaf", "checkout", new_leaf)
    _git(child, "add", "nested leaf")
    new_child = _commit(child, "Update child")
    _git(repo / "nested child", "fetch", "origin")
    _git(repo / "nested child", "checkout", new_child)
    _git(repo, "add", "nested child")
    _commit(repo, "Update parent")

    project._clone_or_update()
    assert cached_module.read_text() == "second = 2\n"


@pytest.mark.parametrize(
    ("cutoff", "use_first"),
    [
        ("2026-04-08T00:00:00Z", True),
        ("2026-04-09T22:00:00Z", False),
        ("2026-04-11T00:00:00Z", False),
        ("2026-03-01T00:00:00Z", False),
    ],
)
def test_project_timestamp_pinning(
    repo: Path, monkeypatch: pytest.MonkeyPatch, cutoff: str, use_first: bool
) -> None:
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-03-01T00:00:00Z")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-04-01T00:00:00Z")
    first = _commit(repo, "Before cutoff")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-04-10T00:00:00+02:00")
    second = _commit(repo, "After cutoff")

    project = _install(repo, exclude_newer=cutoff)
    assert project.current_commit == (first if use_first else second)


def test_project_timestamp_pinning_failed_fetch(
    repo: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    sha = _commit(repo, "Initial commit")
    project = _install(repo)
    project._exclude_newer = "2000-01-01T00:00:00Z"
    _git(
        project.root_directory, "remote", "set-url", "origin", str(tmp_path / "missing")
    )

    project._pin_to_timestamp()

    assert project.current_commit == sha
    assert "failed to deepen clone, using HEAD as-is" in caplog.text


@pytest.mark.parametrize("paths", [None, ["scripts"]])
def test_script_discovery(
    repo: Path, monkeypatch: pytest.MonkeyPatch, paths: list[str] | None
) -> None:
    script_names = ["a space.py", "a\nnewline.py", "café.pyi"]
    scripts = repo / "scripts"
    scripts.mkdir()
    for name in [*script_names, "notes.md"]:
        (scripts / name).write_text("# /// script\n")
    (scripts / "ordinary.py").write_text("x = 1\n")
    (repo / " leading space.py").write_text("# /// script\n")
    _git(repo, "add", ".")
    _commit(repo, "Add scripts")
    project = _install(repo, paths=paths)
    (project.root_directory / "scripts" / "untracked.py").write_text("# /// script\n")
    monkeypatch.setenv("UV", "true")

    with patch(
        "ecosystem_analyzer.installed_project.subprocess.run", wraps=subprocess.run
    ) as run:
        project._install_script_dependencies()

    sync_commands = [c.args[0] for c in run.call_args_list if c.args[0][0] == "true"]
    expected = {str(project.root_directory / "scripts" / name) for name in script_names}
    if paths is None:
        expected.add(str(project.root_directory / " leading space.py"))
    assert len(sync_commands) == 2 * len(expected)
    assert {cmd[cmd.index("--script") + 1] for cmd in sync_commands} == expected


def test_script_discovery_without_matches(repo: Path) -> None:
    _commit(repo, "No scripts")
    project = _install(repo)

    with patch(
        "ecosystem_analyzer.installed_project.subprocess.run", wraps=subprocess.run
    ) as run:
        project._install_script_dependencies()

    assert run.call_count == 1


def test_script_discovery_reports_git_errors(repo: Path, tmp_path: Path) -> None:
    _commit(repo, "Initial commit")
    project = _install(repo)
    project._cache_path = tmp_path

    with pytest.raises(subprocess.CalledProcessError) as error:
        project._install_script_dependencies()

    assert error.value.returncode == 128

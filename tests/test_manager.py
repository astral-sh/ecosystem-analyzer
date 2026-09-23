"""Check installation and comparison scheduling without network access."""

import json
from pathlib import Path
from threading import Event
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from mypy_primer.model import Project

from ecosystem_analyzer.installed_project import InstalledProject
from ecosystem_analyzer.main import cli
from ecosystem_analyzer.manager import Manager
from ecosystem_analyzer.schema import RunOutput
from ecosystem_analyzer.ty import Ty


def _projects() -> dict[str, Project]:
    return {
        name: Project(
            location=f"https://example.com/{name}", mypy_cmd=None, pyright_cmd=None
        )
        for name in ("slow", "ready")
    }


def _output(ty: Ty, project: InstalledProject, runs: int = 0) -> RunOutput:
    return RunOutput(
        project=project.name,
        strict_settings=False,
        ty_commit=ty.commit_sha,
        diagnostics=[],
        flaky_diagnostics=[],
        exit_statuses=[{"return_code": 0, "count": max(runs, 1), "panic_messages": []}],
        flaky_runs=runs,
        median_time_s=0.0,
    )


@pytest.fixture(autouse=True)
def isolated_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(InstalledProject, "_clone_or_update", lambda _: None)


def test_prebuilt_diff_checks_ready_projects(tmp_path: Path) -> None:
    """Both revisions finish the ready project before the slow install completes."""
    slow_started = Event()
    ready_compared = Event()
    checked: list[tuple[str, str, int]] = []

    def install(project: InstalledProject) -> None:
        if project.name == "slow":
            slow_started.set()
            assert ready_compared.wait(5), "Checking waited for every installation"
        else:
            assert slow_started.wait(5)

    def check(ty: Ty, project: InstalledProject, runs: int = 0) -> RunOutput:
        checked.append((project.name, ty.commit_sha, runs))
        if (project.name, ty.commit_sha) == ("ready", "new"):
            ready_compared.set()
        return _output(ty, project, runs)

    binary = tmp_path / "ty"
    binary.touch()
    flaky = tmp_path / "flaky.txt"
    flaky.write_text("ready\n")
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    with (
        patch(
            "ecosystem_analyzer.main.get_ecosystem_projects", return_value=_projects()
        ),
        patch.object(InstalledProject, "_install_dependencies", install),
        patch.object(Ty, "run_on_project", check),
        patch.object(Ty, "run_on_project_multiple", check),
    ):
        result = CliRunner().invoke(
            cli,
            [
                "--flaky-runs",
                "3",
                "diff",
                "--ty-binary-old",
                str(binary),
                "--old",
                "old",
                "--ty-binary-new",
                str(binary),
                "--new",
                "new",
                "--projects-flaky",
                str(flaky),
                "--output-old",
                str(old),
                "--output-new",
                str(new),
            ],
        )

    assert result.exit_code == 0, result.output
    assert checked == [
        ("ready", "old", 3),
        ("ready", "new", 3),
        ("slow", "old", 0),
        ("slow", "new", 0),
    ]
    for path, commit in ((old, "old"), (new, "new")):
        outputs = json.loads(path.read_text())["outputs"]
        assert [output["project"] for output in outputs] == ["ready", "slow"]
        assert all(output["ty_commit"] == commit for output in outputs)
        assert [output["flaky_runs"] for output in outputs] == [3, 0]


@pytest.mark.parametrize("prebuilt_diff", [True, False])
def test_comparisons_reuse_installed_projects(
    tmp_path: Path, prebuilt_diff: bool
) -> None:
    """Later revisions reuse the same checkout and virtual environment."""
    installed: list[InstalledProject] = []
    checked: list[InstalledProject] = []

    def install(project: InstalledProject) -> None:
        installed.append(project)

    def check(ty: Ty, project: InstalledProject) -> RunOutput:
        checked.append(project)
        return _output(ty, project)

    with (
        patch.object(InstalledProject, "_install_dependencies", install),
        patch.object(Ty, "run_on_project", check),
    ):
        manager = Manager(
            target_dir=None, project_names=["ready"], ecosystem_projects=_projects()
        )
        if prebuilt_diff:
            manager.run_prebuilt_diff(
                old_binary=tmp_path / "old",
                old_commit="old",
                new_binary=tmp_path / "new",
                new_commit="new",
            )
        else:
            for commit in ("old", "new"):
                manager.use_prebuilt(tmp_path / commit, commit)
                manager.run_projects()
        manager.use_prebuilt(tmp_path / "third", "third")
        [output] = manager.run_projects()

    assert len(installed) == 1
    assert checked == installed * 3
    assert output["ty_commit"] == "third"


@pytest.mark.parametrize("failure", ["install", "check"])
def test_prebuilt_diff_propagates_failures(tmp_path: Path, failure: str) -> None:
    """Installation and checker exceptions must not produce a partial comparison."""

    def install(_: InstalledProject) -> None:
        if failure == "install":
            raise RuntimeError("installation failed")

    def check(_: Ty, __: InstalledProject) -> RunOutput:
        raise RuntimeError("checking failed")

    with (
        patch.object(InstalledProject, "_install_dependencies", install),
        patch.object(Ty, "run_on_project", check),
    ):
        manager = Manager(
            target_dir=None, project_names=["ready"], ecosystem_projects=_projects()
        )
        with pytest.raises(RuntimeError, match="failed"):
            manager.run_prebuilt_diff(
                old_binary=tmp_path / "old",
                old_commit="old",
                new_binary=tmp_path / "new",
                new_commit="new",
            )

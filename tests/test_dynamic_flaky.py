"""Tests for dynamic flaky detection (Ty.run_on_project_dynamic)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from ecosystem_analyzer.diagnostic import Diagnostic
from ecosystem_analyzer.flaky import diagnostic_keys
from ecosystem_analyzer.main import cli
from ecosystem_analyzer.manager import Manager
from ecosystem_analyzer.run_output import RunOutput
from ecosystem_analyzer.ty import Ty


def _diag(
    path: str = "a.py",
    line: int = 1,
    column: int = 1,
    message: str = "msg",
    lint_name: str = "some-lint",
    level: str = "error",
) -> Diagnostic:
    return Diagnostic(
        path=path,
        line=line,
        column=column,
        level=level,
        lint_name=lint_name,
        message=message,
    )


def _run_output(
    diagnostics: list[Diagnostic],
    *,
    project: str = "proj",
    time_s: float | None = 1.0,
    return_code: int | None = 1,
) -> RunOutput:
    return RunOutput({
        "project": project,
        "project_location": f"https://github.com/example/{project}",
        "ty_commit": "abc123",
        "diagnostics": diagnostics,
        "time_s": time_s,
        "return_code": return_code,
    })


@pytest.fixture
def ty() -> Ty:
    """Create a Ty instance with a mocked repository."""
    repo = MagicMock()
    repo.head.commit.hexsha = "abc123"
    return Ty(repo, None)


def _patch_run_on_project(
    monkeypatch: pytest.MonkeyPatch, ty: Ty, mock: MagicMock
) -> MagicMock:
    monkeypatch.setattr(ty, "run_on_project", mock)
    return mock


class TestDiagnosticKeys:
    def test_empty(self):
        assert diagnostic_keys([]) == frozenset()

    def test_identity(self):
        d = _diag("a.py", 1, 1, "msg")
        keys = diagnostic_keys([d])
        assert len(keys) == 1

    def test_duplicates_collapsed(self):
        d = _diag("a.py", 1, 1, "msg")
        keys = diagnostic_keys([d, d])
        assert len(keys) == 1

    def test_different_diagnostics(self):
        d1 = _diag("a.py", 1, 1, "msg1")
        d2 = _diag("a.py", 2, 1, "msg2")
        keys = diagnostic_keys([d1, d2])
        assert len(keys) == 2


class TestRunOnProjectDynamic:
    """Tests for Ty.run_on_project_dynamic."""

    def test_skip_reruns_when_no_changes(self, ty: Ty, monkeypatch: pytest.MonkeyPatch):
        """When first run matches baseline, all reruns are skipped."""
        d1 = _diag("a.py", 1, 1, "stable msg")
        baseline = _run_output([d1])

        project = MagicMock()
        project.name = "proj"
        project.location = "https://github.com/example/proj"

        # run_on_project is called only once (the first run)
        mock = _patch_run_on_project(
            monkeypatch, ty, MagicMock(return_value=_run_output([d1]))
        )

        result = ty.run_on_project_dynamic(project, max_runs=10, baseline=baseline)

        assert mock.call_count == 1
        assert result["diagnostics"] == [d1]
        # No flaky detection was done, so no flaky_runs key
        assert "flaky_runs" not in result
        assert "flaky_diagnostics" not in result

    def test_short_circuit_all_changes_flaky(
        self, ty: Ty, monkeypatch: pytest.MonkeyPatch
    ):
        """When all changes vs baseline are flaky, detection short-circuits."""
        stable = _diag("a.py", 1, 1, "stable msg")
        flaky = _diag("a.py", 5, 1, "flaky msg")
        baseline = _run_output([stable])

        project = MagicMock()
        project.name = "proj"
        project.location = "https://github.com/example/proj"

        # Run 1: stable + flaky → changes detected vs baseline
        # Run 2: stable only → flaky is absent → classified as flaky
        # After run 2: stable_keys == baseline_keys → short-circuit
        call_count = 0

        def mock_run(p):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _run_output([stable, flaky])
            else:
                return _run_output([stable])

        mock = _patch_run_on_project(monkeypatch, ty, MagicMock(side_effect=mock_run))

        result = ty.run_on_project_dynamic(project, max_runs=10, baseline=baseline)

        # Should stop after 2 runs (short-circuit)
        assert mock.call_count == 2
        assert result["flaky_runs"] == 2
        assert len(result["diagnostics"]) == 1
        assert result["diagnostics"][0]["message"] == "stable msg"
        assert "flaky_diagnostics" in result

    def test_run_all_when_real_changes(self, ty: Ty, monkeypatch: pytest.MonkeyPatch):
        """When there are real (non-flaky) changes, all runs are executed."""
        old_diag = _diag("a.py", 1, 1, "old msg")
        new_diag = _diag("a.py", 2, 1, "new msg")
        baseline = _run_output([old_diag])

        project = MagicMock()
        project.name = "proj"
        project.location = "https://github.com/example/proj"

        # Every run returns the new diagnostic consistently → it's stable
        # But it differs from baseline → never short-circuits
        mock = _patch_run_on_project(
            monkeypatch, ty, MagicMock(return_value=_run_output([new_diag]))
        )

        result = ty.run_on_project_dynamic(project, max_runs=5, baseline=baseline)

        assert mock.call_count == 5
        assert result["flaky_runs"] == 5
        assert len(result["diagnostics"]) == 1
        assert result["diagnostics"][0]["message"] == "new msg"

    def test_no_baseline_new_project_all_flaky(
        self, ty: Ty, monkeypatch: pytest.MonkeyPatch
    ):
        """New project (no baseline): short-circuits when all diagnostics are flaky."""
        flaky_diag = _diag("a.py", 1, 1, "flaky")

        project = MagicMock()
        project.name = "new_proj"
        project.location = "https://github.com/example/new_proj"

        call_count = 0

        def mock_run(p):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _run_output([flaky_diag], project="new_proj")
            else:
                # Second run has no diagnostics → first diagnostic is flaky
                return _run_output([], project="new_proj")

        mock = _patch_run_on_project(monkeypatch, ty, MagicMock(side_effect=mock_run))

        result = ty.run_on_project_dynamic(project, max_runs=10, baseline=None)

        # baseline_keys is empty, after run 2 stable is empty → match → short-circuit
        assert mock.call_count == 2
        assert result["flaky_runs"] == 2
        assert len(result["diagnostics"]) == 0
        assert "flaky_diagnostics" in result

    def test_no_baseline_new_project_stable_diagnostics(
        self, ty: Ty, monkeypatch: pytest.MonkeyPatch
    ):
        """New project with stable diagnostics: runs all since they differ from empty baseline."""
        d = _diag("a.py", 1, 1, "stable")

        project = MagicMock()
        project.name = "new_proj"
        project.location = "https://github.com/example/new_proj"

        mock = _patch_run_on_project(
            monkeypatch,
            ty,
            MagicMock(return_value=_run_output([d], project="new_proj")),
        )

        result = ty.run_on_project_dynamic(project, max_runs=3, baseline=None)

        # stable_keys = {d} != frozenset() → never short-circuits
        assert mock.call_count == 3
        assert result["flaky_runs"] == 3
        assert len(result["diagnostics"]) == 1

    def test_abort_on_abnormal_exit_first_run(
        self, ty: Ty, monkeypatch: pytest.MonkeyPatch
    ):
        """Abnormal exit on first run aborts immediately."""
        baseline = _run_output([])

        project = MagicMock()
        project.name = "proj"
        project.location = "https://github.com/example/proj"

        mock = _patch_run_on_project(
            monkeypatch,
            ty,
            MagicMock(return_value=_run_output([], return_code=101)),
        )

        result = ty.run_on_project_dynamic(project, max_runs=10, baseline=baseline)

        assert mock.call_count == 1
        assert result["return_code"] == 101

    def test_abort_on_timeout_first_run(self, ty: Ty, monkeypatch: pytest.MonkeyPatch):
        """Timeout on first run aborts immediately."""
        baseline = _run_output([])

        project = MagicMock()
        project.name = "proj"
        project.location = "https://github.com/example/proj"

        mock = _patch_run_on_project(
            monkeypatch,
            ty,
            MagicMock(return_value=_run_output([], return_code=None, time_s=None)),
        )

        result = ty.run_on_project_dynamic(project, max_runs=10, baseline=baseline)

        assert mock.call_count == 1
        assert result["return_code"] is None

    def test_abort_on_abnormal_exit_later_run(
        self, ty: Ty, monkeypatch: pytest.MonkeyPatch
    ):
        """Abnormal exit on a later run aborts flaky detection."""
        stable = _diag("a.py", 1, 1, "msg")
        flaky = _diag("a.py", 5, 1, "flaky")
        baseline = _run_output([stable])

        project = MagicMock()
        project.name = "proj"
        project.location = "https://github.com/example/proj"

        call_count = 0

        def mock_run(p):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Changes detected → triggers reruns
                return _run_output([stable, flaky])
            else:
                # Second run fails
                return _run_output([], return_code=101)

        mock = _patch_run_on_project(monkeypatch, ty, MagicMock(side_effect=mock_run))

        result = ty.run_on_project_dynamic(project, max_runs=10, baseline=baseline)

        assert mock.call_count == 2
        assert result["return_code"] == 101

    def test_short_circuit_after_three_runs(
        self, ty: Ty, monkeypatch: pytest.MonkeyPatch
    ):
        """Short-circuit can happen after run 3 if not possible after run 2."""
        stable = _diag("a.py", 1, 1, "stable")
        flaky = _diag("a.py", 5, 1, "flaky")
        baseline = _run_output([stable])

        project = MagicMock()
        project.name = "proj"
        project.location = "https://github.com/example/proj"

        call_count = 0

        def mock_run(p):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                # Runs 1 and 2 both have the flaky diagnostic
                return _run_output([stable, flaky])
            else:
                # Run 3: flaky disappears → now classified as flaky (2/3 runs)
                return _run_output([stable])

        mock = _patch_run_on_project(monkeypatch, ty, MagicMock(side_effect=mock_run))

        result = ty.run_on_project_dynamic(project, max_runs=10, baseline=baseline)

        # After run 2: both runs contain {stable, flaky} → stable_keys =
        # {stable, flaky} ≠ baseline_keys {stable} → no short-circuit.
        # After run 3: flaky appears in 2/3 runs → classified as flaky (not
        # stable) → stable_keys = {stable} == baseline_keys → short-circuit.
        assert mock.call_count == 3
        assert result["flaky_runs"] == 3

    def test_median_time_and_return_code(self, ty: Ty, monkeypatch: pytest.MonkeyPatch):
        """Result uses median time and most common return code from completed runs."""
        d = _diag("a.py", 1, 1, "msg")
        # Baseline differs so we trigger reruns
        baseline = _run_output([])

        project = MagicMock()
        project.name = "proj"
        project.location = "https://github.com/example/proj"

        times = [3.0, 1.0, 2.0]
        call_count = 0

        def mock_run(p):
            nonlocal call_count
            t = times[call_count]
            call_count += 1
            return _run_output([d], time_s=t, return_code=1)

        _patch_run_on_project(monkeypatch, ty, MagicMock(side_effect=mock_run))

        result = ty.run_on_project_dynamic(project, max_runs=3, baseline=baseline)

        assert result["time_s"] == 2.0  # median of [1.0, 2.0, 3.0]
        assert result["return_code"] == 1

    def test_first_run_empty_matches_empty_baseline(
        self, ty: Ty, monkeypatch: pytest.MonkeyPatch
    ):
        """First run with no diagnostics matches empty baseline → skip reruns."""
        baseline = _run_output([])

        project = MagicMock()
        project.name = "proj"
        project.location = "https://github.com/example/proj"

        mock = _patch_run_on_project(
            monkeypatch, ty, MagicMock(return_value=_run_output([]))
        )

        result = ty.run_on_project_dynamic(project, max_runs=10, baseline=baseline)

        assert mock.call_count == 1
        assert len(result["diagnostics"]) == 0

    def test_max_runs_less_than_two_rejected(self, ty: Ty):
        """max_runs < 2 triggers the assertion (use run_on_project for N=1)."""
        project = MagicMock()
        project.name = "proj"
        with pytest.raises(AssertionError):
            ty.run_on_project_dynamic(project, max_runs=1, baseline=None)


def _make_manager(
    *,
    flaky_runs: int = 1,
    flaky_projects: set[str] | None = None,
) -> tuple[Manager, MagicMock]:
    """Build a Manager without running ``__init__`` (which would spawn threads).

    Returns the manager along with the ``MagicMock`` standing in for ``_ty``,
    so tests can configure return values and make assertions without going
    through the typed ``Manager._ty: Ty`` attribute.
    """
    manager = Manager.__new__(Manager)
    manager._flaky_runs = flaky_runs
    manager._flaky_projects = flaky_projects or set()
    ty_mock = MagicMock()
    manager._ty = ty_mock
    manager._active_projects = []
    manager._install_future = None
    return manager, ty_mock


def _make_project(name: str) -> MagicMock:
    project = MagicMock()
    project.name = name
    project.location = f"https://github.com/example/{name}"
    return project


class TestManagerIsFlakyProject:
    def test_flaky_runs_one_never_flaky(self):
        manager, _ = _make_manager(flaky_runs=1)
        project = _make_project("p")
        assert manager._is_flaky_project(project) is False

    def test_flaky_runs_gt_one_no_filter_always_flaky(self):
        manager, _ = _make_manager(flaky_runs=3)
        assert manager._is_flaky_project(_make_project("anything")) is True

    def test_flaky_projects_filter_includes(self):
        manager, _ = _make_manager(flaky_runs=3, flaky_projects={"allowed"})
        assert manager._is_flaky_project(_make_project("allowed")) is True
        assert manager._is_flaky_project(_make_project("other")) is False


class TestManagerRunActiveProjects:
    """Tests for Manager._run_active_projects dispatch logic."""

    def test_no_flaky_uses_single_run(self):
        manager, ty_mock = _make_manager(flaky_runs=1)
        project = _make_project("p")
        manager._active_projects = [project]
        ty_mock.run_on_project.return_value = _run_output([], project="p")

        manager._run_active_projects()

        ty_mock.run_on_project.assert_called_once_with(project)
        ty_mock.run_on_project_multiple.assert_not_called()
        ty_mock.run_on_project_dynamic.assert_not_called()

    def test_flaky_without_baseline_uses_multiple(self):
        manager, ty_mock = _make_manager(flaky_runs=5)
        project = _make_project("p")
        manager._active_projects = [project]
        ty_mock.run_on_project_multiple.return_value = _run_output([], project="p")

        manager._run_active_projects()

        ty_mock.run_on_project_multiple.assert_called_once_with(project, 5)
        ty_mock.run_on_project.assert_not_called()
        ty_mock.run_on_project_dynamic.assert_not_called()

    def test_flaky_with_baseline_uses_dynamic(self):
        manager, ty_mock = _make_manager(flaky_runs=5)
        project = _make_project("p")
        manager._active_projects = [project]
        ty_mock.run_on_project_dynamic.return_value = _run_output([], project="p")

        baseline_output = _run_output([], project="p")
        manager._run_active_projects(baseline=[baseline_output])

        ty_mock.run_on_project_dynamic.assert_called_once_with(
            project, 5, baseline_output
        )
        ty_mock.run_on_project.assert_not_called()
        ty_mock.run_on_project_multiple.assert_not_called()

    def test_baseline_missing_project_passes_none(self):
        """A project not present in the baseline is passed baseline=None."""
        manager, ty_mock = _make_manager(flaky_runs=3)
        project = _make_project("new_project")
        manager._active_projects = [project]
        ty_mock.run_on_project_dynamic.return_value = _run_output(
            [], project="new_project"
        )

        other_baseline = _run_output([], project="different_project")
        manager._run_active_projects(baseline=[other_baseline])

        ty_mock.run_on_project_dynamic.assert_called_once_with(project, 3, None)

    def test_single_run_bypasses_flaky(self):
        """single_run=True forces single-run dispatch even when flaky_runs > 1."""
        manager, ty_mock = _make_manager(flaky_runs=5)
        project = _make_project("p")
        manager._active_projects = [project]
        ty_mock.run_on_project.return_value = _run_output([], project="p")

        manager._run_active_projects(single_run=True)

        ty_mock.run_on_project.assert_called_once_with(project)
        ty_mock.run_on_project_multiple.assert_not_called()
        ty_mock.run_on_project_dynamic.assert_not_called()

    def test_non_flaky_project_with_baseline_still_single_run(self):
        """With --projects-flaky filter, non-flaky projects run once even with baseline."""
        manager, ty_mock = _make_manager(flaky_runs=3, flaky_projects={"flaky_p"})
        flaky_p = _make_project("flaky_p")
        stable_p = _make_project("stable_p")
        manager._active_projects = [flaky_p, stable_p]
        ty_mock.run_on_project.side_effect = lambda p: _run_output([], project=p.name)
        ty_mock.run_on_project_dynamic.return_value = _run_output([], project="flaky_p")

        manager._run_active_projects(baseline=[_run_output([], project="flaky_p")])

        ty_mock.run_on_project.assert_called_once_with(stable_p)
        ty_mock.run_on_project_dynamic.assert_called_once()
        ty_mock.run_on_project_multiple.assert_not_called()

    def test_baseline_lookup_by_project_name(self):
        """Each project gets its own baseline entry, matched by project name."""
        manager, ty_mock = _make_manager(flaky_runs=3)
        p1 = _make_project("p1")
        p2 = _make_project("p2")
        manager._active_projects = [p1, p2]

        baselines = [_run_output([], project="p1"), _run_output([], project="p2")]
        ty_mock.run_on_project_dynamic.side_effect = lambda p, n, b: _run_output(
            [], project=p.name
        )

        manager._run_active_projects(baseline=baselines)

        calls = ty_mock.run_on_project_dynamic.call_args_list
        assert calls[0].args == (p1, 3, baselines[0])
        assert calls[1].args == (p2, 3, baselines[1])


class TestDynamicFlakyCliValidation:
    """Tests for the --dynamic-flaky flag's CLI validation."""

    def test_rejects_flaky_runs_below_two(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Avoid touching a real ty repository.
        monkeypatch.setattr(
            "ecosystem_analyzer.main.resolve_ty_repo", lambda path: path
        )

        projects_old = tmp_path / "old.txt"
        projects_new = tmp_path / "new.txt"
        projects_old.write_text("")
        projects_new.write_text("")

        result = CliRunner().invoke(
            cli,
            [
                "--repository",
                str(tmp_path),
                "--flaky-runs",
                "1",
                "diff",
                "--projects-old",
                str(projects_old),
                "--projects-new",
                str(projects_new),
                "--old",
                "abc",
                "--new",
                "def",
                "--dynamic-flaky",
            ],
        )

        assert result.exit_code == 1
        assert "--dynamic-flaky requires --flaky-runs >= 2" in result.output

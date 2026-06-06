from pathlib import Path
from unittest.mock import MagicMock, patch

from ecosystem_analyzer.installed_project import InstalledProject
from ecosystem_analyzer.run_output import ExitStatus, OutputVariant, RunOutput
from ecosystem_analyzer.ty import Ty


def _output(
    return_code: int | None,
    *,
    diagnostics: list | None = None,
    time_s: float | None = None,
    panic_messages: list[str] | None = None,
    stderr: str | None = None,
) -> RunOutput:
    exit_status = ExitStatus(return_code=return_code, count=1)
    if panic_messages:
        exit_status["panic_messages"] = [
            OutputVariant(message=message, count=1) for message in panic_messages
        ]
    if stderr:
        exit_status["stderr"] = [OutputVariant(message=stderr, count=1)]
    output = RunOutput({
        "project": "proj",
        "project_location": "https://github.com/example/proj",
        "ty_commit": "abc123",
        "diagnostics": diagnostics or [],
        "exit_statuses": [exit_status],
        "median_time_s": time_s,
    })
    return output


def _ty() -> Ty:
    ty = Ty()
    ty.use_prebuilt(Path("ty"), "abc123")
    return ty


def _project() -> InstalledProject:
    project = MagicMock(spec=InstalledProject)
    project.name = "proj"
    project.location = "https://github.com/example/proj"
    return project


def test_multiple_runs_classify_intermittent_abnormal_exit_as_flaky():
    diagnostic = {
        "level": "error",
        "lint_name": "some-lint",
        "path": "a.py",
        "line": 1,
        "column": 1,
        "message": "only emitted by successful runs",
    }
    outputs = [
        _output(2, panic_messages=["intermittent panic"], stderr="crashed"),
        _output(1, diagnostics=[diagnostic], time_s=1.0),
        _output(1, diagnostics=[diagnostic], time_s=2.0),
    ]
    ty = _ty()

    with patch.object(ty, "run_on_project", side_effect=outputs) as run:
        result = ty.run_on_project_multiple(_project(), 3)

    assert run.call_count == 3
    assert result["exit_statuses"] == [
        {"return_code": 1, "count": 2},
        {
            "return_code": 2,
            "count": 1,
            "panic_messages": [{"message": "intermittent panic", "count": 1}],
            "stderr": [{"message": "crashed", "count": 1}],
        },
    ]
    assert result["diagnostics"] == []
    assert result["flaky_diagnostics"][0]["variants"][0]["count"] == 2
    assert "return_code" not in result
    assert "time_s" not in result
    assert "panic_messages" not in result
    assert "stderr" not in result


def test_multiple_runs_classify_intermittent_timeout_as_flaky():
    outputs = [
        _output(1, time_s=1.0),
        _output(None),
        _output(1, time_s=2.0),
    ]
    ty = _ty()

    with patch.object(ty, "run_on_project", side_effect=outputs) as run:
        result = ty.run_on_project_multiple(_project(), 3)

    assert run.call_count == 3
    assert result["exit_statuses"] == [
        {"return_code": 1, "count": 2},
        {"return_code": None, "count": 1},
    ]


def test_multiple_runs_preserve_stable_abnormal_exit_evidence():
    panic_messages = [
        "Panicked at crates/ty_python_semantic/src/types/infer.rs:10:2: `bug`\n"
        f"info: Version: 0.0.{version}"
        for version in range(3)
    ]
    outputs = [
        _output(2, panic_messages=[panic], stderr="stable stderr")
        for panic in panic_messages
    ]
    ty = _ty()

    with patch.object(ty, "run_on_project", side_effect=outputs):
        result = ty.run_on_project_multiple(_project(), 3)

    assert result["median_time_s"] is None
    assert result["exit_statuses"] == [
        {
            "return_code": 2,
            "count": 3,
            "panic_messages": [{"message": panic_messages[0], "count": 3}],
            "stderr": [{"message": "stable stderr", "count": 3}],
        }
    ]


def test_multiple_runs_preserve_stable_timeout():
    ty = _ty()

    with patch.object(ty, "run_on_project", side_effect=[_output(None)] * 3):
        result = ty.run_on_project_multiple(_project(), 3)

    assert result["exit_statuses"] == [{"return_code": None, "count": 3}]
    assert result["median_time_s"] is None


def test_multiple_runs_preserve_mixed_status_frequencies():
    outputs = [
        _output(2),
        _output(2),
        _output(1, time_s=1.0),
    ]
    ty = _ty()

    with patch.object(ty, "run_on_project", side_effect=outputs):
        result = ty.run_on_project_multiple(_project(), 3)

    assert result["exit_statuses"] == [
        {"return_code": 1, "count": 1},
        {"return_code": 2, "count": 2},
    ]

import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal

import pytest

from ecosystem_analyzer import diff as diff_module
from ecosystem_analyzer.diff import DiagnosticDiff
from ecosystem_analyzer.schema import Diagnostic, FlakyLocation, RunData, RunOutput

BRANCH = "audit<svg/onload=document.title='REPORT_XSS'>"
PATH = "src/');document.title='PATH_XSS';//<&\".py"


class ParsedReport(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.text: list[str] = []
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def diagnostic(path: str = PATH, message: str = "new") -> Diagnostic:
    return {
        "level": "error",
        "lint_name": "invalid-argument-type",
        "path": path,
        "line": 1,
        "column": 1,
        "message": message,
    }


def output(
    project: str,
    diagnostics: list[Diagnostic],
    flaky_diagnostics: list[FlakyLocation] | None = None,
) -> RunOutput:
    return {
        "project": project,
        "project_location": "https://github.com/example/project",
        "strict_settings": False,
        "ty_commit": "abc123def456",
        "diagnostics": diagnostics,
        "exit_statuses": [{"return_code": 1, "count": 1, "panic_messages": []}],
        "median_time_s": 1.0,
        "flaky_runs": 1,
        "flaky_diagnostics": flaky_diagnostics or [],
    }


def make_diff(
    tmp_path: Path, old_outputs: list[RunOutput], new_outputs: list[RunOutput]
) -> DiagnosticDiff:
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    old_path.write_text(json.dumps(RunData(outputs=old_outputs)))
    new_path.write_text(json.dumps(RunData(outputs=new_outputs)))
    return DiagnosticDiff(
        str(old_path), str(new_path), old_name=BRANCH, new_name=BRANCH
    )


@pytest.mark.parametrize("report_kind", ["diagnostics", "timing"])
@pytest.mark.parametrize("use_fallback_loader", [False, True])
def test_report_escapes_labels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    report_kind: Literal["diagnostics", "timing"],
    use_fallback_loader: bool,
) -> None:
    # This is a valid branch name, not just arbitrary invalid Git input.
    subprocess.run(["git", "check-ref-format", "--branch", BRANCH], check=True)
    if use_fallback_loader:

        def unavailable_package_loader(*_args: str) -> None:
            raise ImportError

        monkeypatch.setattr(diff_module, "PackageLoader", unavailable_package_loader)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "templates").symlink_to(
            Path(diff_module.__file__).parent / "templates", target_is_directory=True
        )

    project = 'project"><svg onload="document.title=\'PROJECT_XSS\'">'
    diff = make_diff(tmp_path, [output(project, [])], [output(project, [diagnostic()])])
    report_path = tmp_path / "report.html"
    if report_kind == "diagnostics":
        diff.generate_html_report(str(report_path))
    else:
        diff.generate_timing_html_report(str(report_path))

    report = ParsedReport(report_path.read_text())
    assert not any(tag == "svg" for tag, _ in report.tags)
    assert "".join(report.text).count(BRANCH) == 2
    assert project in "".join(report.text)


@pytest.mark.parametrize(
    "change",
    [
        "added-project",
        "removed-project",
        "added-file",
        "removed-file",
        "modified-file",
        "flaky-file",
    ],
)
def test_copy_paths_are_literal_data(tmp_path: Path, change: str) -> None:
    old_outputs = [output("project", [])]
    new_outputs = [output("project", [diagnostic()])]
    if change == "added-project":
        old_outputs = []
    elif change == "removed-project":
        old_outputs, new_outputs = new_outputs, []
    elif change == "removed-file":
        old_outputs, new_outputs = new_outputs, old_outputs
    elif change == "modified-file":
        old_outputs = [output("project", [diagnostic(message="old")])]
    elif change == "flaky-file":
        new_outputs = [
            output(
                "project",
                [],
                [
                    {
                        "path": PATH,
                        "line": 1,
                        "column": 1,
                        "variants": [{"diagnostic": diagnostic(), "count": 1}],
                    }
                ],
            )
        ]
    diff = make_diff(tmp_path, old_outputs, new_outputs)
    report_path = tmp_path / "report.html"
    diff.generate_html_report(str(report_path))
    report = ParsedReport(report_path.read_text())
    buttons = [
        attrs
        for tag, attrs in report.tags
        if tag == "button" and attrs.get("class") == "copy-btn"
    ]
    assert len(buttons) == 1
    assert buttons[0]["data-copy-path"] == PATH
    assert PATH in "".join(report.text)
    assert not any(name.startswith("on") for _, attrs in report.tags for name in attrs)


def test_show_more_has_no_inline_handler(tmp_path: Path) -> None:
    diff = make_diff(
        tmp_path,
        [output(f"project-{index}", []) for index in range(25)],
        [output(f"project-{index}", [diagnostic()]) for index in range(25)],
    )
    report_path = tmp_path / "report.html"
    diff.generate_html_report(str(report_path))
    report = ParsedReport(report_path.read_text())
    assert any(attrs.get("class") == "show-more-btn" for _, attrs in report.tags)
    assert not any(name.startswith("on") for _, attrs in report.tags for name in attrs)

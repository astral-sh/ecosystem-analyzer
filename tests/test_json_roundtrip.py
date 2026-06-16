import json
import tempfile
from pathlib import Path
from typing import Any

from ecosystem_analyzer.diff import DiagnosticDiff
from ecosystem_analyzer.run_output import ExitStatus, OutputVariant


def _make_output(
    project: str,
    diagnostics: list,
    flaky_diagnostics: list | None = None,
    flaky_runs: int | None = None,
    exit_statuses: list | None = None,
    panic_messages: list[str] | None = None,
    stderr: str | None = None,
    time_s: float | None = 1.5,
    return_code: int | None = 1,
    project_metadata: dict | None = None,
):
    run_count = flaky_runs or 1
    if exit_statuses is None:
        exit_status = ExitStatus(return_code=return_code, count=run_count)
        if panic_messages is not None:
            exit_status["panic_messages"] = [
                OutputVariant(message=message, count=run_count)
                for message in panic_messages
            ]
        if stderr is not None:
            exit_status["stderr"] = [OutputVariant(message=stderr, count=run_count)]
        exit_statuses = [exit_status]

    entry: dict[str, Any] = {
        "project": project,
        "project_location": f"https://github.com/example/{project}",
        "ty_commit": "abc123def456",
        "diagnostics": diagnostics,
        "exit_statuses": exit_statuses,
        "median_time_s": time_s,
    }
    if flaky_diagnostics is not None:
        entry["flaky_diagnostics"] = flaky_diagnostics
    if flaky_runs is not None:
        entry["flaky_runs"] = flaky_runs
    if project_metadata is not None:
        entry["project_metadata"] = project_metadata
    return entry


def _make_variant(
    path, line, column, message, count, lint_name="some-lint", level="error"
):
    return {
        "diagnostic": {
            "level": level,
            "lint_name": lint_name,
            "path": path,
            "line": line,
            "column": column,
            "message": message,
        },
        "count": count,
    }


def _make_flaky_loc(path, line, column, variants):
    return {"path": path, "line": line, "column": column, "variants": variants}


def _make_failed_output(
    project: str,
    *,
    panic_messages: list[str] | None = None,
    stderr: str | None = None,
    return_code: int | None = 101,
):
    return _make_output(
        project,
        [],
        panic_messages=panic_messages,
        stderr=stderr,
        time_s=None,
        return_code=return_code,
    )


def _make_diff(old_outputs, new_outputs):
    paths = []
    for outputs in (old_outputs, new_outputs):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as file:
            json.dump({"outputs": outputs}, file)
            paths.append(file.name)
    return DiagnosticDiff(paths[0], paths[1])


def _render_html(diff: DiagnosticDiff) -> str:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as file:
        html_path = file.name
    diff.generate_html_report(html_path)
    with open(html_path) as file:
        return file.read()


class TestJsonRoundtrip:
    def test_project_metadata_is_rendered_as_filterable_html(self):
        old_data = {
            "outputs": [
                _make_output(
                    "example-project",
                    [],
                    project_metadata={"kind": "example-kind"},
                )
            ]
        }
        new_data = {
            "outputs": [
                _make_output(
                    "example-project",
                    [
                        {
                            "level": "error",
                            "lint_name": "some-lint",
                            "path": "a.py",
                            "line": 1,
                            "column": 1,
                            "message": "new",
                        }
                    ],
                    project_metadata={"kind": "example-kind"},
                )
            ]
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump(old_data, f1)
            old_path = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump(new_data, f2)
            new_path = f2.name
        with tempfile.NamedTemporaryFile(
            mode="r", suffix=".html", delete=False
        ) as html:
            html_path = html.name

        diff = DiagnosticDiff(old_path, new_path)
        diff.generate_html_report(html_path)
        rendered = Path(html_path).read_text()

        assert 'id="project-kind-filter"' in rendered
        assert 'data-project-kind="example-kind"' in rendered
        assert diff.diffs["modified_projects"][0]["project_metadata"] == {
            "kind": "example-kind"
        }

    def test_roundtrip_without_flaky(self):
        """JSON files without flaky data load and diff correctly."""
        diag = {
            "level": "error",
            "lint_name": "some-lint",
            "path": "a.py",
            "line": 1,
            "column": 1,
            "message": "msg",
        }

        data = {"outputs": [_make_output("proj", [diag])]}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = f.name

        diff = DiagnosticDiff(path, path)
        stats = diff._calculate_statistics()

        assert stats["total_added"] == 0
        assert stats["total_removed"] == 0

    def test_flaky_same_on_both_sides_no_diff(self):
        """When flaky locations have identical variants on both sides, no diff."""
        diag = {
            "level": "error",
            "lint_name": "some-lint",
            "path": "a.py",
            "line": 1,
            "column": 1,
            "message": "stable",
        }
        flaky = [
            _make_flaky_loc(
                "b.py", 10, 1, [_make_variant("b.py", 10, 1, "variant A", count=2)]
            )
        ]

        data = {"outputs": [_make_output("proj", [diag], flaky, flaky_runs=3)]}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = f.name

        diff = DiagnosticDiff(path, path)
        assert len(diff.diffs["modified_projects"]) == 0
        stats = diff._calculate_statistics()
        assert stats["total_added"] == 0
        assert stats["total_removed"] == 0

    def test_flaky_same_location_different_variants_suppressed(self):
        """Flaky locations at the same position are suppressed even with different variants."""
        diag = {
            "level": "error",
            "lint_name": "some-lint",
            "path": "a.py",
            "line": 1,
            "column": 1,
            "message": "stable",
        }
        old_flaky = [
            _make_flaky_loc(
                "b.py",
                10,
                1,
                [_make_variant("b.py", 10, 1, "only old variant", count=1)],
            )
        ]
        new_flaky = [
            _make_flaky_loc(
                "b.py",
                10,
                1,
                [
                    _make_variant("b.py", 10, 1, "only new variant", count=2),
                ],
            )
        ]

        old_data = {"outputs": [_make_output("proj", [diag], old_flaky, flaky_runs=3)]}
        new_data = {"outputs": [_make_output("proj", [diag], new_flaky, flaky_runs=3)]}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump(old_data, f1)
            old_path = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump(new_data, f2)
            new_path = f2.name

        diff = DiagnosticDiff(old_path, new_path)
        stats = diff._calculate_statistics()
        # Same location on both sides → suppressed as statistical noise
        assert stats["total_changed"] == 0
        assert stats["total_added"] == 0
        assert stats["total_removed"] == 0

    def test_flaky_diffs_organized_by_file(self):
        """Flaky diffs are organized by file path for inline rendering."""
        old_data = {"outputs": [_make_output("proj", [])]}
        new_flaky = [
            _make_flaky_loc(
                "a.py", 10, 1, [_make_variant("a.py", 10, 1, "msg1", count=1)]
            ),
            _make_flaky_loc(
                "b.py", 20, 1, [_make_variant("b.py", 20, 1, "msg2", count=2)]
            ),
        ]
        new_data = {"outputs": [_make_output("proj", [], new_flaky, flaky_runs=3)]}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump(old_data, f1)
            old_path = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump(new_data, f2)
            new_path = f2.name

        diff = DiagnosticDiff(old_path, new_path)
        proj = diff.diffs["modified_projects"][0]
        assert "flaky_file_diffs" in proj
        assert "a.py" in proj["flaky_file_diffs"]
        assert "b.py" in proj["flaky_file_diffs"]
        assert len(proj["flaky_file_diffs"]["a.py"]["added"]) == 1
        assert len(proj["flaky_file_diffs"]["b.py"]["added"]) == 1

    def test_failed_project_preserves_panic_messages(self):
        diff = _make_diff(
            [
                _make_failed_output(
                    "proj", panic_messages=["thread 'main' panicked at old panic"]
                )
            ],
            [
                _make_failed_output(
                    "proj", panic_messages=["thread 'main' panicked at new panic"]
                )
            ],
        )
        failed = diff.diffs["failed_projects"][0]
        assert failed["old_panic_messages"] == ["thread 'main' panicked at old panic"]
        assert failed["new_panic_messages"] == ["thread 'main' panicked at new panic"]

    def test_failure_status_categorizes_new_persistent_and_fixed(self):
        shared = "thread 'main' panicked at shared site"
        old_only = "thread 'main' panicked at old-only site"
        new_only = "thread 'main' panicked at new-only site"

        diff = _make_diff(
            [
                _make_output(
                    "introduced",
                    [],
                    panic_messages=[shared],
                    time_s=1.0,
                    return_code=1,
                ),
                _make_failed_output("fixed", panic_messages=[old_only]),
                _make_failed_output("persistent", panic_messages=[shared]),
            ],
            [
                _make_failed_output("introduced", panic_messages=[shared, new_only]),
                _make_output("fixed", [], time_s=1.0, return_code=1),
                _make_failed_output("persistent", panic_messages=[shared]),
            ],
        )
        statuses = {
            entry["project"]: entry["failure_status"]
            for entry in diff.diffs["failed_projects"]
        }

        assert statuses == {
            "introduced": "new",
            "fixed": "fixed",
            "persistent": "persistent",
        }

        introduced_entry = next(
            e for e in diff.diffs["failed_projects"] if e["project"] == "introduced"
        )
        assert introduced_entry["introduced_panic_messages"] == [new_only]
        assert introduced_entry["persistent_panic_messages"] == [shared]
        assert introduced_entry["fixed_panic_messages"] == []

        fixed_entry = next(
            e for e in diff.diffs["failed_projects"] if e["project"] == "fixed"
        )
        assert fixed_entry["fixed_panic_messages"] == [old_only]
        assert fixed_entry["introduced_panic_messages"] == []

        assert "new crashes detected" in diff.generate_comment_title()

        markdown = diff.render_statistics_markdown()
        assert "❌ newly failing" in markdown
        assert "🎉 crashes fixed" in markdown
        # Persistent failures are intentionally omitted from the PR-comment
        # summary table (they appear in the HTML report instead).
        assert "➖ persistent" not in markdown

        html = _render_html(diff)
        assert 'title="Failure introduced by this PR"' in html
        assert (
            'title="Failure that existed on the baseline is no longer present"' in html
        )
        assert 'title="Same failure on both baseline and PR"' in html

    def test_comment_title_celebrates_when_only_panic_change_is_a_fix(self):
        diff = _make_diff(
            [
                _make_failed_output(
                    "proj", panic_messages=["thread 'main' panicked at old"]
                )
            ],
            [_make_output("proj", [], time_s=1.0, return_code=1)],
        )
        title = diff.generate_comment_title()
        assert "🎉" in title
        assert "fixed" in title.lower()
        assert "new panics detected" not in title

        markdown = diff.render_statistics_markdown()
        assert "🎉" in markdown
        assert "🎉 crashes fixed" in markdown

    def test_new_and_fixed_timeouts_are_called_out(self):
        """Timeouts get the same banner treatment as panics."""
        diff = _make_diff(
            [
                _make_output("newly-timing-out", [], time_s=1.0, return_code=0),
                _make_failed_output("newly-passing", return_code=None),
            ],
            [
                _make_failed_output("newly-timing-out", return_code=None),
                _make_output("newly-passing", [], time_s=1.0, return_code=0),
            ],
        )
        markdown = diff.render_statistics_markdown()
        assert "| `newly-timing-out` | ❌ newly failing |" in markdown
        assert "| `newly-passing` | 🎉 crashes fixed |" in markdown

    def test_new_and_fixed_abnormal_exits_without_panics(self):
        """Stack-overflow-style crashes (no panic message) get parity too."""
        diff = _make_diff(
            [
                _make_output("newly-crashing", [], time_s=0.5, return_code=1),
                _make_failed_output("newly-passing", return_code=139),  # e.g. signal
            ],
            [
                _make_failed_output("newly-crashing", return_code=139),
                _make_output("newly-passing", [], time_s=1.0, return_code=0),
            ],
        )
        title = diff.generate_comment_title()
        assert title == "## `ecosystem-analyzer` results: new crashes detected ❌"

        markdown = diff.render_statistics_markdown()
        assert "| `newly-crashing` | ❌ newly failing |" in markdown
        assert "| `newly-passing` | 🎉 crashes fixed |" in markdown

    def test_changed_failure_modes_are_neutral_but_visible(self):
        old_panic = "thread 'main' panicked at old site"
        diff = _make_diff(
            [
                _make_failed_output("panic-to-timeout", panic_messages=[old_panic]),
                _make_failed_output("panic-to-segfault", panic_messages=[old_panic]),
                _make_failed_output("timeout-to-abnormal-exit", return_code=None),
            ],
            [
                _make_failed_output("panic-to-timeout", return_code=None),
                _make_failed_output("panic-to-segfault", return_code=139),
                _make_failed_output("timeout-to-abnormal-exit"),
            ],
        )
        statuses = {
            entry["project"]: entry["failure_status"]
            for entry in diff.diffs["failed_projects"]
        }

        assert statuses == {
            "panic-to-segfault": "changed",
            "panic-to-timeout": "changed",
            "timeout-to-abnormal-exit": "changed",
        }
        assert diff.generate_comment_title() == "## `ecosystem-analyzer` results"

        markdown = diff.render_statistics_markdown()
        assert "| `panic-to-segfault` | ➖ failure mode changed |" in markdown
        assert "| `panic-to-timeout` | ➖ failure mode changed |" in markdown
        assert "| `timeout-to-abnormal-exit` | ➖ failure mode changed |" in markdown
        assert "❌ newly failing" not in markdown
        assert "🎉" not in markdown
        assert (
            "  FAILURE MODE CHANGED old=abnormal exit(exit 101) "
            "new=timeout(timeout)" in markdown
        )
        assert (
            "  FAILURE MODE CHANGED old=timeout(timeout) "
            "new=abnormal exit(exit 101)" in markdown
        )
        assert (
            "  FAILURE MODE CHANGED old=abnormal exit(exit 101) "
            "new=abnormal exit(exit 139)" in markdown
        )
        assert "PARTIAL FIX" not in markdown

        html = _render_html(diff)

        assert "➖ failure mode changed" in html
        assert 'title="Failure mode changed between the baseline and PR"' in html
        assert "🎉" not in html
        assert "➖ Previous panic message (no longer present)" in html
        assert "Fixed panic message" not in html

    def test_introduced_panics_outrank_changed_failure_modes(self):
        shared = "thread 'main' panicked at shared site"
        introduced = "thread 'main' panicked at new site"
        diff = _make_diff(
            [_make_failed_output("mixed-regression", panic_messages=[shared])],
            [
                _make_failed_output(
                    "mixed-regression",
                    panic_messages=[shared, introduced],
                    return_code=139,
                ),
            ],
        )
        entry = diff.diffs["failed_projects"][0]

        assert entry["failure_status"] == "new_panics"
        assert entry["introduced_panic_messages"] == [introduced]
        assert entry["persistent_panic_messages"] == [shared]
        assert diff.has_new_panics()
        assert "new panics detected" in diff.generate_comment_title()

        markdown = diff.render_statistics_markdown()
        assert "| `mixed-regression` | ❌ new panics |" in markdown
        assert "- NEW PANIC: 1 introduced, project still failing" in markdown
        assert "❌ newly failing" not in markdown
        assert "FAILURE MODE CHANGED" not in markdown

        html = _render_html(diff)

        assert "Panic regression introduced" in html
        assert "New ecosystem failure introduced" not in html
        assert "❌ new panics" in html
        assert (
            'title="New panic messages introduced by this PR while the project '
            'remains failing"'
        ) in html

    def test_html_report_escapes_panic_messages(self):
        introduced = "</pre><script>alert('introduced')</script><pre>"
        fixed = "<img src=x onerror=alert('fixed')>"
        persistent = "<svg onload=alert('persistent')>"
        diff = _make_diff(
            [_make_failed_output("proj", panic_messages=[fixed, persistent])],
            [_make_failed_output("proj", panic_messages=[introduced, persistent])],
        )
        html = _render_html(diff)

        assert introduced not in html
        assert fixed not in html
        assert persistent not in html
        assert "&lt;/pre&gt;&lt;script&gt;" in html
        assert "&lt;img src=x onerror=alert" in html
        assert "&lt;svg onload=alert" in html

    def test_persistent_failures_hidden_from_markdown_table(self):
        """Projects failing on both sides are kept out of the PR-comment
        summary table but remain in the full diff data (for the HTML report)."""
        persistent_panic = "thread 'main' panicked at shared site"
        diff = _make_diff(
            [
                _make_failed_output("still-broken", panic_messages=[persistent_panic]),
                _make_output("regressed", [], time_s=1.0, return_code=0),
            ],
            [
                _make_failed_output("still-broken", panic_messages=[persistent_panic]),
                _make_failed_output(
                    "regressed",
                    panic_messages=["thread 'main' panicked at new site"],
                ),
            ],
        )

        # The persistent project still appears in the structured diff
        # data (which feeds the HTML report).
        assert any(
            p["project"] == "still-broken" for p in diff.diffs["failed_projects"]
        )

        markdown = diff.render_statistics_markdown()

        # Header is rendered (since `regressed` still populates the table).
        assert "**Failing projects**:" in markdown
        # Regressed project is in the table; persistent one is not.
        assert "| `regressed` |" in markdown
        # Persistent project should be absent from the entire comment
        # (summary table AND raw diff section).
        assert "still-broken" not in markdown

    def test_persistent_only_suppresses_failing_projects_table(self):
        """When the only failures are persistent, no failing-projects table."""
        persistent_panic = "thread 'main' panicked at shared site"
        diff = _make_diff(
            [_make_failed_output("still-broken", panic_messages=[persistent_panic])],
            [_make_failed_output("still-broken", panic_messages=[persistent_panic])],
        )
        markdown = diff.render_statistics_markdown()
        assert diff.generate_comment_title() == "## `ecosystem-analyzer` results"
        assert "**Failing projects**:" not in markdown

    def test_changed_stderr_is_visible_only_in_html_report(self):
        old_stderr = "invalid invocation: <old option>"
        new_stderr = "invalid invocation: <new option>"
        diff = _make_diff(
            [_make_failed_output("same-code-error", stderr=old_stderr, return_code=2)],
            [_make_failed_output("same-code-error", stderr=new_stderr, return_code=2)],
        )
        entry = diff.diffs["failed_projects"][0]

        assert entry["failure_status"] == "persistent"

        markdown = diff.render_statistics_markdown()
        assert "same-code-error" not in markdown
        assert old_stderr not in markdown
        assert new_stderr not in markdown

        html = _render_html(diff)
        assert "same-code-error" in html
        assert html.count("<summary>stderr (1/1 runs)</summary>") == 2
        assert "invalid invocation: &lt;old option&gt;" in html
        assert "invalid invocation: &lt;new option&gt;" in html

    def test_panic_location_and_trace_changes_are_persistent(self):
        old_panics = {
            "backtrace": """Panicked at crates/ty_python_semantic/src/types/signatures.rs:1719:42 when checking `/tmp/project.py`: `internal error`
info: This indicates a bug in ty.
info: Backtrace:
   0: ty_python_semantic::types::signatures::Signature::new
             at crates/ty_python_semantic/src/types/signatures.rs:1719:42""",
            "query-stack": """Panicked at crates/ty_python_semantic/src/types/infer.rs:70:9 when checking `/tmp/project.py`: `query error`
info: This indicates a bug in ty.
info: query stacktrace:
   0: infer_definition_types(Id(b633))
             at crates/ty_python_semantic/src/types/infer.rs:70""",
        }
        new_panics = {
            "backtrace": """Panicked at crates/ty_python_semantic/src/types/signatures.rs:1720:42 when checking `/tmp/project.py`: `internal error`
info: This indicates a bug in ty.
info: Backtrace:
   1: ty_python_semantic::types::signatures::Signature::new
             at crates/ty_python_semantic/src/types/signatures.rs:1720:42""",
            "query-stack": """Panicked at crates/ty_python_semantic/src/types/infer.rs:71:9 when checking `/tmp/project.py`: `query error`
info: This indicates a bug in ty.
info: query stacktrace:
   0: infer_definition_types(Id(b744))
             at crates/ty_python_semantic/src/types/infer.rs:71""",
        }
        diff = _make_diff(
            [
                _make_failed_output(project, panic_messages=[panic])
                for project, panic in old_panics.items()
            ],
            [
                _make_failed_output(project, panic_messages=[panic])
                for project, panic in new_panics.items()
            ],
        )
        entries = {entry["project"]: entry for entry in diff.diffs["failed_projects"]}

        assert set(entries) == set(new_panics)
        for project, new_panic in new_panics.items():
            entry = entries[project]
            assert entry["failure_status"] == "persistent"
            assert entry["introduced_panic_messages"] == []
            assert entry["fixed_panic_messages"] == []
            assert entry["persistent_panic_messages"] == [new_panic]
            assert entry["old_panic_messages"] == [old_panics[project]]
            assert entry["new_panic_messages"] == [new_panic]

        assert not diff.has_new_panics()
        assert diff.generate_comment_title() == "## `ecosystem-analyzer` results"

    def test_panic_version_and_args_changes_are_persistent(self):
        old_panic = """Panicked at somewhere: `internal error`
info: This indicates a bug in ty.
info: Version: 0.0.1
info: Args: /tmp/old_commit/ty check ."""
        new_panic = """Panicked at somewhere: `internal error`
info: This indicates a bug in ty.
info: Version: 0.0.2
info: Args: /tmp/new_commit/ty check ."""
        diff = _make_diff(
            [_make_failed_output("proj", panic_messages=[old_panic])],
            [_make_failed_output("proj", panic_messages=[new_panic])],
        )
        entry = diff.diffs["failed_projects"][0]

        assert entry["failure_status"] == "persistent"
        assert entry["introduced_panic_messages"] == []
        assert entry["fixed_panic_messages"] == []
        assert entry["persistent_panic_messages"] == [new_panic]
        assert not diff.has_new_panics()

    def test_multiple_panics_with_same_normalized_key_preserve_multiplicity(self):
        def panic_at(line: int) -> str:
            return (
                "Panicked at crates/ty_python_semantic/src/types/infer.rs:"
                f"{line}:9 when checking `/tmp/project.py`: `query error`"
            )

        two_panics = [panic_at(70), panic_at(80)]
        three_panics = [panic_at(60), *two_panics]
        diff = _make_diff(
            [
                _make_failed_output("added-site", panic_messages=two_panics),
                _make_failed_output("removed-site", panic_messages=three_panics),
            ],
            [
                _make_failed_output("added-site", panic_messages=three_panics),
                _make_failed_output("removed-site", panic_messages=two_panics),
            ],
        )
        entries = {entry["project"]: entry for entry in diff.diffs["failed_projects"]}

        added_entry = entries["added-site"]
        assert added_entry["failure_status"] == "new_panics"
        assert added_entry["introduced_panic_messages"] == [panic_at(60)]
        assert added_entry["fixed_panic_messages"] == []
        assert added_entry["persistent_panic_messages"] == two_panics

        removed_entry = entries["removed-site"]
        assert removed_entry["failure_status"] == "reduced"
        assert removed_entry["introduced_panic_messages"] == []
        assert removed_entry["fixed_panic_messages"] == [panic_at(60)]
        assert removed_entry["persistent_panic_messages"] == two_panics

    def test_introduced_project_failures_detects_new_abnormal_exit_code(self):
        diff = _make_diff(
            [_make_output("proj", [], time_s=1.5, return_code=1)],
            [
                _make_failed_output(
                    "proj", panic_messages=["thread 'main' panicked at new panic"]
                )
            ],
        )
        introduced_failures = diff.introduced_project_failures()

        assert introduced_failures == ["proj"]

    def test_introduced_project_failures_detects_new_timeouts(self):
        diff = _make_diff(
            [_make_output("proj", [], time_s=1.5, return_code=0)],
            [_make_failed_output("proj", return_code=None)],
        )

        assert diff.introduced_project_failures() == ["proj"]

    def test_intermittent_abnormal_exit_is_excluded_as_flaky(self):
        diff = _make_diff(
            [_make_output("proj", [], flaky_runs=3, return_code=1)],
            [
                _make_output(
                    "proj",
                    [],
                    flaky_runs=3,
                    exit_statuses=[
                        {"return_code": 1, "count": 1},
                        {
                            "return_code": 2,
                            "count": 2,
                            "panic_messages": [
                                {"message": "intermittent <panic>", "count": 2}
                            ],
                            "stderr": [
                                {"message": "intermittent <stderr>", "count": 1}
                            ],
                        },
                    ],
                    return_code=1,
                )
            ],
        )

        assert diff.diffs["failed_projects"] == []
        assert diff.introduced_project_failures() == []
        assert len(diff.diffs["flaky_exit_status_changes"]) == 1
        assert diff.generate_comment_title() == "## `ecosystem-analyzer` results"

        markdown = diff.render_statistics_markdown()
        assert "excludes flaky changes" in markdown
        assert "new crashes detected" not in markdown

        html = _render_html(diff)
        assert "Flaky Exit Status Changes" in html
        assert "(2/3)" in html
        assert "exit code <code>2</code>" in html
        assert "panic (2/3 runs)" in html
        assert "intermittent &lt;panic&gt;" in html
        assert "stderr (1/3 runs)" in html
        assert "intermittent &lt;stderr&gt;" in html

    def test_intermittent_timeout_is_excluded_as_flaky(self):
        diff = _make_diff(
            [_make_output("proj", [], flaky_runs=3, return_code=1)],
            [
                _make_output(
                    "proj",
                    [],
                    flaky_runs=3,
                    exit_statuses=[
                        {"return_code": 1, "count": 2},
                        {"return_code": None, "count": 1},
                    ],
                    return_code=1,
                )
            ],
        )

        assert diff.diffs["failed_projects"] == []
        assert diff.introduced_project_failures() == []
        assert "timeout" in _render_html(diff)

    def test_stable_failure_to_intermittent_success_is_not_fixed(self):
        diff = _make_diff(
            [_make_failed_output("proj", return_code=2)],
            [
                _make_output(
                    "proj",
                    [],
                    flaky_runs=3,
                    exit_statuses=[
                        {"return_code": 1, "count": 1},
                        {"return_code": 2, "count": 2},
                    ],
                    return_code=1,
                )
            ],
        )

        assert diff.diffs["failed_projects"] == []
        assert "ecosystem failure fixed" not in diff.generate_comment_title()
        assert len(diff.diffs["flaky_exit_status_changes"]) == 1

    def test_intermittent_failure_to_stable_failure_is_not_new(self):
        diff = _make_diff(
            [
                _make_output(
                    "proj",
                    [],
                    flaky_runs=3,
                    exit_statuses=[
                        {"return_code": 1, "count": 2},
                        {"return_code": 2, "count": 1},
                    ],
                    return_code=1,
                )
            ],
            [_make_failed_output("proj", return_code=2)],
        )

        assert diff.diffs["failed_projects"] == []
        assert diff.introduced_project_failures() == []
        assert len(diff.diffs["flaky_exit_status_changes"]) == 1

    def test_varying_abnormal_exit_codes_still_form_stable_failure(self):
        diff = _make_diff(
            [_make_output("proj", [], flaky_runs=3, return_code=1)],
            [
                _make_output(
                    "proj",
                    [],
                    flaky_runs=3,
                    exit_statuses=[
                        {"return_code": 2, "count": 2},
                        {"return_code": 3, "count": 1},
                    ],
                    time_s=None,
                    return_code=2,
                )
            ],
        )

        assert diff.introduced_project_failures() == ["proj"]
        assert diff.diffs["failed_projects"][0]["failure_status"] == "new"

    def test_stable_stack_overflow_with_varying_thread_ids_is_not_flaky(self):
        stack_overflows = [
            {
                "message": (
                    f"thread '<unknown>' ({thread_id}) has overflowed its stack\n"
                    "fatal runtime error: stack overflow, aborting"
                ),
                "count": 1,
            }
            for thread_id in range(10)
        ]
        diff = _make_diff(
            [_make_output("proj", [], flaky_runs=10, return_code=1)],
            [
                _make_output(
                    "proj",
                    [],
                    flaky_runs=10,
                    exit_statuses=[
                        {
                            "return_code": -6,
                            "count": 10,
                            "stderr": stack_overflows,
                        }
                    ],
                    time_s=None,
                )
            ],
        )

        assert diff.diffs["flaky_exit_status_changes"] == []
        assert diff.diffs["failed_projects"][0]["failure_status"] == "new"
        html = _render_html(diff)
        assert "Failed Projects" in html
        assert "Flaky Exit Status Changes" not in html

    def test_varying_failure_codes_on_both_sides_are_persistent(self):
        old_statuses = [
            {"return_code": 2, "count": 2},
            {"return_code": 3, "count": 1},
        ]
        new_statuses = [
            {"return_code": 2, "count": 1},
            {"return_code": 3, "count": 2},
        ]
        diff = _make_diff(
            [
                _make_output(
                    "proj",
                    [],
                    flaky_runs=3,
                    exit_statuses=old_statuses,
                    time_s=None,
                    return_code=2,
                )
            ],
            [
                _make_output(
                    "proj",
                    [],
                    flaky_runs=3,
                    exit_statuses=new_statuses,
                    time_s=None,
                    return_code=3,
                )
            ],
        )

        assert diff.diffs["flaky_exit_status_changes"] == []
        assert diff.diffs["failed_projects"][0]["failure_status"] == "persistent"

    def test_stable_diagnostic_changes_survive_flaky_exit_status(self):
        old_diagnostic = {
            "level": "error",
            "lint_name": "some-lint",
            "path": "a.py",
            "line": 1,
            "column": 1,
            "message": "old",
        }
        new_diagnostic = {
            **old_diagnostic,
            "line": 2,
            "message": "new stable diagnostic",
        }
        diff = _make_diff(
            [_make_output("proj", [old_diagnostic], flaky_runs=3, return_code=1)],
            [
                _make_output(
                    "proj",
                    [old_diagnostic, new_diagnostic],
                    flaky_runs=3,
                    exit_statuses=[
                        {"return_code": 1, "count": 2},
                        {"return_code": 2, "count": 1},
                    ],
                    return_code=1,
                )
            ],
        )

        assert diff._calculate_statistics()["total_added"] == 1

    def test_added_project_preserves_flaky_exit_status_evidence(self):
        output = _make_output(
            "new-project",
            [],
            flaky_runs=3,
            exit_statuses=[
                {"return_code": 1, "count": 2},
                {
                    "return_code": 2,
                    "count": 1,
                    "stderr": [{"message": "new project crashed", "count": 1}],
                },
            ],
        )
        diff = _make_diff([], [output])

        assert len(diff.diffs["flaky_exit_status_changes"]) == 1
        assert "excludes flaky changes" in diff.render_statistics_markdown()
        html = _render_html(diff)
        assert "Flaky Exit Status Changes" in html
        assert "new project crashed" in html
        assert "not present" in html

    def test_removed_project_preserves_flaky_exit_status_evidence(self):
        output = _make_output(
            "old-project",
            [],
            flaky_runs=3,
            exit_statuses=[
                {"return_code": 1, "count": 2},
                {
                    "return_code": None,
                    "count": 1,
                    "stderr": [{"message": "old project timed out", "count": 1}],
                },
            ],
        )
        diff = _make_diff([output], [])

        assert len(diff.diffs["flaky_exit_status_changes"]) == 1
        assert "excludes flaky changes" in diff.render_statistics_markdown()
        html = _render_html(diff)
        assert "Flaky Exit Status Changes" in html
        assert "old project timed out" in html
        assert "not present" in html

    def test_flaky_exit_evidence_changes_with_same_statuses_are_preserved(self):
        old_statuses = [
            {"return_code": 1, "count": 2},
            {
                "return_code": 2,
                "count": 1,
                "panic_messages": [{"message": "old panic", "count": 1}],
                "stderr": [{"message": "old stderr", "count": 1}],
            },
        ]
        new_statuses = [
            {"return_code": 1, "count": 2},
            {
                "return_code": 2,
                "count": 1,
                "panic_messages": [{"message": "new panic", "count": 1}],
                "stderr": [{"message": "new stderr", "count": 1}],
            },
        ]
        diff = _make_diff(
            [_make_output("proj", [], flaky_runs=3, exit_statuses=old_statuses)],
            [_make_output("proj", [], flaky_runs=3, exit_statuses=new_statuses)],
        )

        assert len(diff.diffs["flaky_exit_status_changes"]) == 1
        html = _render_html(diff)
        assert "old panic" in html
        assert "new panic" in html
        assert "old stderr" in html
        assert "new stderr" in html

    def test_intermittent_panic_change_with_one_exit_status_is_preserved(self):
        old_statuses = [
            {
                "return_code": 101,
                "count": 3,
                "panic_messages": [{"message": "panic A", "count": 3}],
            }
        ]
        new_statuses = [
            {
                "return_code": 101,
                "count": 3,
                "panic_messages": [
                    {"message": "panic A", "count": 2},
                    {"message": "panic B", "count": 1},
                ],
            }
        ]
        diff = _make_diff(
            [
                _make_output(
                    "proj",
                    [],
                    flaky_runs=3,
                    time_s=None,
                    exit_statuses=old_statuses,
                )
            ],
            [
                _make_output(
                    "proj",
                    [],
                    flaky_runs=3,
                    time_s=None,
                    exit_statuses=new_statuses,
                )
            ],
        )

        assert len(diff.diffs["flaky_exit_status_changes"]) == 1
        assert diff.diffs["failed_projects"][0]["failure_status"] == "persistent"
        assert "panics reduced" not in diff.generate_comment_title()
        html = _render_html(diff)
        assert "panic A" in html
        assert "panic B" in html

    def test_added_and_removed_projects_render_stable_exit_evidence(self):
        added = _make_output(
            "added",
            [],
            return_code=2,
            panic_messages=["added panic"],
            stderr="added stderr",
        )
        removed = _make_output(
            "removed",
            [],
            return_code=2,
            panic_messages=["removed panic"],
            stderr="removed stderr",
        )
        diff = _make_diff([removed], [added])

        assert (
            diff.diffs["added_projects"][0]["exit_statuses"] == added["exit_statuses"]
        )
        assert (
            diff.diffs["removed_projects"][0]["exit_statuses"]
            == removed["exit_statuses"]
        )
        html = _render_html(diff)
        assert "added panic" in html
        assert "added stderr" in html
        assert "removed panic" in html
        assert "removed stderr" in html

    def test_flaky_exit_frequency_changes_are_ignored(self):
        old_statuses = [
            {"return_code": 1, "count": 9},
            {"return_code": 2, "count": 1},
        ]
        new_statuses = [
            {"return_code": 1, "count": 1},
            {"return_code": 2, "count": 9},
        ]
        diff = _make_diff(
            [_make_output("proj", [], flaky_runs=10, exit_statuses=old_statuses)],
            [_make_output("proj", [], flaky_runs=10, exit_statuses=new_statuses)],
        )

        assert diff.diffs["flaky_exit_status_changes"] == []
        assert diff.diffs["failed_projects"] == []

    def test_comment_title_combines_panics_crashes_and_timeouts(self):
        diff = _make_diff(
            [
                _make_output("panic_proj", [], time_s=1.5, return_code=0),
                _make_output("crash_proj", [], time_s=1.5, return_code=0),
                _make_output("timeout_proj", [], time_s=1.5, return_code=0),
            ],
            [
                _make_failed_output(
                    "panic_proj",
                    panic_messages=["thread 'main' panicked at new panic"],
                ),
                _make_failed_output("crash_proj", return_code=-11),
                _make_failed_output("timeout_proj", return_code=None),
            ],
        )
        assert diff.generate_comment_title() == (
            "## `ecosystem-analyzer` results: new crashes detected ❌"
        )

    def test_comment_title_for_only_new_timeouts(self):
        """When every new failure is a timeout, say so — calling them
        'crashes' would be misleading."""
        diff = _make_diff(
            [
                _make_output("a", [], time_s=1.5, return_code=0),
                _make_output("b", [], time_s=1.5, return_code=0),
            ],
            [
                _make_failed_output("a", return_code=None),
                _make_failed_output("b", return_code=None),
            ],
        )
        assert diff.generate_comment_title() == (
            "## `ecosystem-analyzer` results: new timeouts detected ❌"
        )

    def test_partial_panic_fix_is_celebrated_precisely(self):
        """A project that loses some (but not all) panics while still
        failing is celebrated without claiming that the project recovered."""
        shared = "thread 'main' panicked at shared"
        old_only = "thread 'main' panicked at old-only"

        diff = _make_diff(
            [_make_failed_output("partial", panic_messages=[shared, old_only])],
            [_make_failed_output("partial", panic_messages=[shared])],
        )

        entry = next(
            e for e in diff.diffs["failed_projects"] if e["project"] == "partial"
        )
        assert entry["failure_status"] == "reduced"
        assert entry["fixed_panic_messages"] == [old_only]

        assert diff.generate_comment_title() == (
            "## `ecosystem-analyzer` results: panics reduced 🎉"
        )

        markdown = diff.render_statistics_markdown()
        assert "| `partial` | 🎉 panics reduced |" in markdown
        assert "🎉 crashes fixed" not in markdown
        assert "PARTIAL FIX" in markdown

        html = _render_html(diff)

        assert "🎉 panics reduced" in html
        assert "🎉 Fixed panic message (no longer present)" in html
        assert (
            'title="Some panic messages that existed on the baseline '
            'are no longer present"'
        ) in html

    def test_disjoint_panics_on_still_failing_project_counts_as_regression(self):
        """A project failing with panic A, then failing with a completely
        different panic B, is a regression — the introduced panic wins over
        the fact that the old panic went away."""
        old_panic = "thread 'main' panicked at old site"
        new_panic = "thread 'main' panicked at new site"

        diff = _make_diff(
            [_make_failed_output("swapped", panic_messages=[old_panic])],
            [_make_failed_output("swapped", panic_messages=[new_panic])],
        )
        entry = next(
            e for e in diff.diffs["failed_projects"] if e["project"] == "swapped"
        )
        assert entry["failure_status"] == "new_panics"
        assert entry["introduced_panic_messages"] == [new_panic]
        assert entry["fixed_panic_messages"] == [old_panic]
        assert entry["persistent_panic_messages"] == []

        assert diff.has_new_panics()
        assert "new panics detected" in diff.generate_comment_title()

        markdown = diff.render_statistics_markdown()
        assert "🎉" not in markdown

        html = _render_html(diff)

        assert "🎉" not in html
        assert "➖ Previous panic message (no longer present)" in html
        assert "Fixed panic message" not in html

    def test_no_flaky_keys_when_absent(self):
        """When no flaky data exists, no flaky keys in output."""
        diag1 = {
            "level": "error",
            "lint_name": "some-lint",
            "path": "a.py",
            "line": 1,
            "column": 1,
            "message": "old",
        }
        diag2 = {
            "level": "error",
            "lint_name": "some-lint",
            "path": "a.py",
            "line": 1,
            "column": 1,
            "message": "new",
        }

        old_data = {"outputs": [_make_output("proj", [diag1])]}
        new_data = {"outputs": [_make_output("proj", [diag2])]}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump(old_data, f1)
            old_path = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump(new_data, f2)
            new_path = f2.name

        diff = DiagnosticDiff(old_path, new_path)
        proj = diff.diffs["modified_projects"][0]
        assert "flaky_diffs" not in proj
        assert "flaky_file_diffs" not in proj

    def test_statistics_markdown_includes_large_timing_changes(self):
        old_data = {
            "outputs": [
                _make_output("slow-project", [], time_s=10.0, return_code=0),
                _make_output("very-fast-project", [], time_s=10.0, return_code=0),
            ]
        }
        new_data = {
            "outputs": [
                _make_output("slow-project", [], time_s=15.0, return_code=0),
                _make_output("very-fast-project", [], time_s=5.0, return_code=0),
            ]
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump(old_data, f1)
            old_path = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump(new_data, f2)
            new_path = f2.name

        diff = DiagnosticDiff(old_path, new_path)
        markdown = diff.render_statistics_markdown()

        assert "**Large timing changes**:" in markdown
        assert "| `slow-project` | 10.00s | 15.00s | +50% |" in markdown
        assert "| `very-fast-project` | 10.00s | 5.00s | -50% |" in markdown

    def test_flaky_diffs_excluded_from_statistics(self):
        """Flaky diffs are excluded from statistics but stable diffs from the same project are kept."""
        stable_diag = {
            "level": "error",
            "lint_name": "some-lint",
            "path": "a.py",
            "line": 1,
            "column": 1,
            "message": "stable",
        }
        new_stable_diag = {
            "level": "error",
            "lint_name": "some-lint",
            "path": "a.py",
            "line": 2,
            "column": 1,
            "message": "new stable diagnostic",
        }
        # Flaky location only on new side (genuinely new flaky location)
        new_flaky = [
            _make_flaky_loc(
                "c.py", 50, 1, [_make_variant("c.py", 50, 1, "flaky variant", count=2)]
            )
        ]

        old_data = {"outputs": [_make_output("proj", [stable_diag])]}
        new_data = {
            "outputs": [
                _make_output(
                    "proj", [stable_diag, new_stable_diag], new_flaky, flaky_runs=3
                )
            ]
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump(old_data, f1)
            old_path = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump(new_data, f2)
            new_path = f2.name

        diff = DiagnosticDiff(old_path, new_path)

        # Only the stable diagnostic is counted; the flaky one is excluded.
        stats = diff._calculate_statistics()
        assert stats["total_added"] == 1
        lint_entry = next(
            e for e in stats["merged_by_lint"] if e["lint_name"] == "some-lint"
        )
        assert lint_entry["added"] == 1

    def test_stable_diffs_kept_from_flaky_projects(self):
        """Stable diagnostic changes from a project with flaky_runs > 1 are still counted."""
        diag = {
            "level": "error",
            "lint_name": "some-lint",
            "path": "a.py",
            "line": 1,
            "column": 1,
            "message": "msg",
        }
        new_diag = {
            "level": "error",
            "lint_name": "some-lint",
            "path": "a.py",
            "line": 2,
            "column": 1,
            "message": "new msg",
        }

        # Project has flaky_runs=5 but no flaky_diagnostics — the stable
        # diagnostic change should still be counted.
        old_data = {"outputs": [_make_output("proj", [diag], flaky_runs=5)]}
        new_data = {"outputs": [_make_output("proj", [diag, new_diag], flaky_runs=5)]}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump(old_data, f1)
            old_path = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump(new_data, f2)
            new_path = f2.name

        diff = DiagnosticDiff(old_path, new_path)
        stats = diff._calculate_statistics()
        assert stats["total_added"] == 1

    def test_flaky_only_project_absent_from_project_breakdown(self):
        """A project whose only changes are flaky does not appear in merged_by_project."""
        diag = {
            "level": "error",
            "lint_name": "some-lint",
            "path": "a.py",
            "line": 1,
            "column": 1,
            "message": "stable",
        }
        new_flaky = [
            _make_flaky_loc(
                "b.py", 5, 1, [_make_variant("b.py", 5, 1, "flaky msg", count=2)]
            )
        ]

        # "stable_proj" has a real diagnostic change; "flaky_proj" only has flaky changes.
        old_data = {
            "outputs": [
                _make_output("stable_proj", [diag]),
                _make_output("flaky_proj", [diag]),
            ]
        }
        new_data = {
            "outputs": [
                _make_output("stable_proj", []),
                _make_output("flaky_proj", [diag], new_flaky, flaky_runs=3),
            ]
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump(old_data, f1)
            old_path = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump(new_data, f2)
            new_path = f2.name

        diff = DiagnosticDiff(old_path, new_path)
        stats = diff._calculate_statistics()
        project_names = [p["project_name"] for p in stats["merged_by_project"]]
        assert "stable_proj" in project_names
        assert "flaky_proj" not in project_names

    def test_flaky_notice_shown_when_flaky_diagnostics_present(self):
        """The rendered markdown includes a notice when flaky diagnostics were excluded."""
        diag = {
            "level": "error",
            "lint_name": "some-lint",
            "path": "a.py",
            "line": 1,
            "column": 1,
            "message": "stable",
        }
        new_flaky = [
            _make_flaky_loc(
                "b.py", 5, 1, [_make_variant("b.py", 5, 1, "flaky msg", count=2)]
            )
        ]

        old_data = {"outputs": [_make_output("proj", [diag])]}
        new_data = {"outputs": [_make_output("proj", [diag], new_flaky, flaky_runs=3)]}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump(old_data, f1)
            old_path = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump(new_data, f2)
            new_path = f2.name

        diff = DiagnosticDiff(old_path, new_path)
        markdown = diff.render_statistics_markdown()
        assert "excludes flaky changes" in markdown

    def test_flaky_notice_absent_when_no_flaky_diagnostics(self):
        """The rendered markdown omits the flaky notice when there are no flaky diagnostics."""
        diag = {
            "level": "error",
            "lint_name": "some-lint",
            "path": "a.py",
            "line": 1,
            "column": 1,
            "message": "stable",
        }
        new_diag = {
            "level": "error",
            "lint_name": "some-lint",
            "path": "a.py",
            "line": 2,
            "column": 1,
            "message": "new stable",
        }

        old_data = {"outputs": [_make_output("proj", [diag])]}
        new_data = {"outputs": [_make_output("proj", [diag, new_diag])]}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump(old_data, f1)
            old_path = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump(new_data, f2)
            new_path = f2.name

        diff = DiagnosticDiff(old_path, new_path)
        markdown = diff.render_statistics_markdown()
        assert "flaky" not in markdown.lower()

    def test_flaky_entries_excluded_from_raw_diff_output(self):
        """Flaky diagnostic messages do not appear in the rendered markdown raw diff."""
        stable_diag = {
            "level": "error",
            "lint_name": "some-lint",
            "path": "a.py",
            "line": 1,
            "column": 1,
            "message": "stable message",
        }
        new_stable_diag = {
            "level": "error",
            "lint_name": "some-lint",
            "path": "a.py",
            "line": 2,
            "column": 1,
            "message": "new stable message",
        }
        new_flaky = [
            _make_flaky_loc(
                "b.py",
                5,
                1,
                [_make_variant("b.py", 5, 1, "flaky variant msg", count=2)],
            )
        ]

        old_data = {"outputs": [_make_output("proj", [stable_diag])]}
        new_data = {
            "outputs": [
                _make_output(
                    "proj", [stable_diag, new_stable_diag], new_flaky, flaky_runs=3
                )
            ]
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump(old_data, f1)
            old_path = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump(new_data, f2)
            new_path = f2.name

        diff = DiagnosticDiff(old_path, new_path)
        markdown = diff.render_statistics_markdown()

        # The stable diagnostic should appear in the raw diff
        assert "new stable message" in markdown
        # The flaky diagnostic should NOT appear in the raw diff
        assert "flaky variant msg" not in markdown

    def test_added_project_with_only_flaky_diagnostics(self):
        """An added project with only flaky diagnostics produces zero stats and shows the notice."""
        flaky = [
            _make_flaky_loc(
                "a.py", 1, 1, [_make_variant("a.py", 1, 1, "flaky only msg", count=3)]
            )
        ]

        old_data = {"outputs": []}
        new_data = {"outputs": [_make_output("new_proj", [], flaky, flaky_runs=3)]}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump(old_data, f1)
            old_path = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump(new_data, f2)
            new_path = f2.name

        diff = DiagnosticDiff(old_path, new_path)
        stats = diff._calculate_statistics()
        assert stats["total_added"] == 0
        assert stats["total_removed"] == 0
        project_names = [p["project_name"] for p in stats["merged_by_project"]]
        assert "new_proj" not in project_names

        markdown = diff.render_statistics_markdown()
        assert "excludes flaky changes" in markdown
        assert "flaky only msg" not in markdown

    def test_removed_project_with_only_flaky_diagnostics(self):
        """A removed project with only flaky diagnostics produces zero stats and shows the notice."""
        flaky = [
            _make_flaky_loc(
                "a.py",
                1,
                1,
                [_make_variant("a.py", 1, 1, "removed flaky msg", count=2)],
            )
        ]

        old_data = {"outputs": [_make_output("old_proj", [], flaky, flaky_runs=3)]}
        new_data = {"outputs": []}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump(old_data, f1)
            old_path = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump(new_data, f2)
            new_path = f2.name

        diff = DiagnosticDiff(old_path, new_path)
        stats = diff._calculate_statistics()
        assert stats["total_added"] == 0
        assert stats["total_removed"] == 0
        project_names = [p["project_name"] for p in stats["merged_by_project"]]
        assert "old_proj" not in project_names

        markdown = diff.render_statistics_markdown()
        assert "excludes flaky changes" in markdown
        assert "removed flaky msg" not in markdown

    def test_statistics_markdown_omits_small_or_failed_timing_changes(self):
        old_data = {
            "outputs": [
                _make_output("medium-change", [], time_s=10.0, return_code=0),
                _make_output("timed-out", [], time_s=10.0, return_code=0),
            ]
        }
        new_data = {
            "outputs": [
                _make_output("medium-change", [], time_s=14.0, return_code=0),
                _make_output("timed-out", [], time_s=None, return_code=101),
            ]
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump(old_data, f1)
            old_path = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump(new_data, f2)
            new_path = f2.name

        diff = DiagnosticDiff(old_path, new_path)
        markdown = diff.render_statistics_markdown()

        assert "**Large timing changes**:" not in markdown
        assert "medium-change" not in markdown
        assert "| Project | Old Time | New Time | Change |" not in markdown

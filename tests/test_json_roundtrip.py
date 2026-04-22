import json
import tempfile

from ecosystem_analyzer.diff import DiagnosticDiff


def _make_output(
    project: str,
    diagnostics: list,
    flaky_diagnostics: list | None = None,
    flaky_runs: int | None = None,
    panic_messages: list[str] | None = None,
    time_s: float | None = 1.5,
    return_code: int | None = 1,
):
    entry = {
        "project": project,
        "project_location": f"https://github.com/example/{project}",
        "ty_commit": "abc123def456",
        "diagnostics": diagnostics,
        "time_s": time_s,
        "return_code": return_code,
    }
    if flaky_diagnostics is not None:
        entry["flaky_diagnostics"] = flaky_diagnostics
    if flaky_runs is not None:
        entry["flaky_runs"] = flaky_runs
    if panic_messages is not None:
        entry["panic_messages"] = panic_messages
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


class TestJsonRoundtrip:
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
        old_data = {
            "outputs": [
                _make_output(
                    "proj",
                    [],
                    panic_messages=["thread 'main' panicked at old panic"],
                    time_s=None,
                    return_code=101,
                )
            ]
        }
        new_data = {
            "outputs": [
                _make_output(
                    "proj",
                    [],
                    panic_messages=["thread 'main' panicked at new panic"],
                    time_s=None,
                    return_code=101,
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
        failed = diff.diffs["failed_projects"][0]
        assert failed["old_panic_messages"] == ["thread 'main' panicked at old panic"]
        assert failed["new_panic_messages"] == ["thread 'main' panicked at new panic"]

    def test_failure_status_categorizes_new_persistent_and_fixed(self):
        shared = "thread 'main' panicked at shared site"
        old_only = "thread 'main' panicked at old-only site"
        new_only = "thread 'main' panicked at new-only site"

        old_data = {
            "outputs": [
                _make_output(
                    "introduced",
                    [],
                    panic_messages=[shared],
                    time_s=1.0,
                    return_code=1,
                ),
                _make_output(
                    "fixed",
                    [],
                    panic_messages=[old_only],
                    time_s=None,
                    return_code=101,
                ),
                _make_output(
                    "persistent",
                    [],
                    panic_messages=[shared],
                    time_s=None,
                    return_code=101,
                ),
            ]
        }
        new_data = {
            "outputs": [
                _make_output(
                    "introduced",
                    [],
                    panic_messages=[shared, new_only],
                    time_s=None,
                    return_code=101,
                ),
                _make_output(
                    "fixed",
                    [],
                    time_s=1.0,
                    return_code=1,
                ),
                _make_output(
                    "persistent",
                    [],
                    panic_messages=[shared],
                    time_s=None,
                    return_code=101,
                ),
            ]
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump(old_data, f1)
            old_path = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump(new_data, f2)
            new_path = f2.name

        diff = DiagnosticDiff(old_path, new_path)
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

        assert diff.has_new_failures()
        assert diff.has_fixed_failures()
        assert "new crashes detected" in diff.generate_comment_title()

        markdown = diff.render_statistics_markdown()
        assert "❌ newly failing" in markdown
        assert "🎉 crashes fixed" in markdown
        # Persistent failures are intentionally omitted from the PR-comment
        # summary table (they appear in the HTML report instead).
        assert "➖ persistent" not in markdown

    def test_comment_title_celebrates_when_only_panic_change_is_a_fix(self):
        old_data = {
            "outputs": [
                _make_output(
                    "proj",
                    [],
                    panic_messages=["thread 'main' panicked at old"],
                    time_s=None,
                    return_code=101,
                )
            ]
        }
        new_data = {"outputs": [_make_output("proj", [], time_s=1.0, return_code=1)]}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump(old_data, f1)
            old_path = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump(new_data, f2)
            new_path = f2.name

        diff = DiagnosticDiff(old_path, new_path)
        title = diff.generate_comment_title()
        assert "🎉" in title
        assert "fixed" in title.lower()
        assert "new panics detected" not in title

        markdown = diff.render_statistics_markdown()
        assert "🎉" in markdown
        assert "🎉 crashes fixed" in markdown

    def test_new_and_fixed_timeouts_are_called_out(self):
        """Timeouts get the same banner treatment as panics."""
        old_data = {
            "outputs": [
                _make_output("newly-timing-out", [], time_s=1.0, return_code=0),
                _make_output("newly-passing", [], time_s=None, return_code=None),
            ]
        }
        new_data = {
            "outputs": [
                _make_output("newly-timing-out", [], time_s=None, return_code=None),
                _make_output("newly-passing", [], time_s=1.0, return_code=0),
            ]
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump(old_data, f1)
            old_path = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump(new_data, f2)
            new_path = f2.name

        diff = DiagnosticDiff(old_path, new_path)
        assert diff.has_new_failures()
        assert diff.has_fixed_failures()

        markdown = diff.render_statistics_markdown()
        assert "| `newly-timing-out` | ❌ newly failing |" in markdown
        assert "| `newly-passing` | 🎉 crashes fixed |" in markdown

    def test_new_and_fixed_abnormal_exits_without_panics(self):
        """Stack-overflow-style crashes (no panic message) get parity too."""
        old_data = {
            "outputs": [
                _make_output("newly-crashing", [], time_s=0.5, return_code=1),
                _make_output(
                    "newly-passing",
                    [],
                    time_s=None,
                    return_code=139,  # e.g. signal
                ),
            ]
        }
        new_data = {
            "outputs": [
                _make_output(
                    "newly-crashing",
                    [],
                    time_s=None,
                    return_code=139,
                ),
                _make_output("newly-passing", [], time_s=1.0, return_code=0),
            ]
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump(old_data, f1)
            old_path = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump(new_data, f2)
            new_path = f2.name

        diff = DiagnosticDiff(old_path, new_path)
        assert diff.has_new_failures()
        assert diff.has_fixed_failures()

        title = diff.generate_comment_title()
        assert "crashes" in title

        markdown = diff.render_statistics_markdown()
        assert "| `newly-crashing` | ❌ newly failing |" in markdown
        assert "| `newly-passing` | 🎉 crashes fixed |" in markdown

    def test_persistent_failures_hidden_from_markdown_table(self):
        """Projects failing on both sides are kept out of the PR-comment
        summary table but remain in the full diff data (for the HTML report)."""
        persistent_panic = "thread 'main' panicked at shared site"
        old_data = {
            "outputs": [
                _make_output(
                    "still-broken",
                    [],
                    panic_messages=[persistent_panic],
                    time_s=None,
                    return_code=101,
                ),
                _make_output("regressed", [], time_s=1.0, return_code=0),
            ]
        }
        new_data = {
            "outputs": [
                _make_output(
                    "still-broken",
                    [],
                    panic_messages=[persistent_panic],
                    time_s=None,
                    return_code=101,
                ),
                _make_output(
                    "regressed",
                    [],
                    panic_messages=["thread 'main' panicked at new site"],
                    time_s=None,
                    return_code=101,
                ),
            ]
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump(old_data, f1)
            old_path = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump(new_data, f2)
            new_path = f2.name

        diff = DiagnosticDiff(old_path, new_path)

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
        old_data = {
            "outputs": [
                _make_output(
                    "still-broken",
                    [],
                    panic_messages=[persistent_panic],
                    time_s=None,
                    return_code=101,
                )
            ]
        }
        new_data = {
            "outputs": [
                _make_output(
                    "still-broken",
                    [],
                    panic_messages=[persistent_panic],
                    time_s=None,
                    return_code=101,
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
        assert "**Failing projects**:" not in markdown

    def test_persistent_panic_does_not_flag_title(self):
        msg = "thread 'main' panicked at shared site"
        old_data = {
            "outputs": [
                _make_output(
                    "proj",
                    [],
                    panic_messages=[msg],
                    time_s=None,
                    return_code=101,
                )
            ]
        }
        new_data = {
            "outputs": [
                _make_output(
                    "proj",
                    [],
                    panic_messages=[msg],
                    time_s=None,
                    return_code=101,
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
        # No new panics, no fixed panics → plain title.
        assert diff.generate_comment_title() == "## `ecosystem-analyzer` results"

    def test_introduced_project_failures_detects_new_abnormal_exit_code(self):
        old_data = {
            "outputs": [
                _make_output(
                    "proj",
                    [],
                    time_s=1.5,
                    return_code=1,
                )
            ]
        }
        new_data = {
            "outputs": [
                _make_output(
                    "proj",
                    [],
                    panic_messages=["thread 'main' panicked at new panic"],
                    time_s=None,
                    return_code=101,
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
        introduced_failures = diff.introduced_project_failures()

        assert introduced_failures == ["proj"]

    def test_introduced_project_failures_detects_new_timeouts(self):
        old_data = {"outputs": [_make_output("proj", [], time_s=1.5, return_code=0)]}
        new_data = {
            "outputs": [_make_output("proj", [], time_s=None, return_code=None)]
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump(old_data, f1)
            old_path = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump(new_data, f2)
            new_path = f2.name

        diff = DiagnosticDiff(old_path, new_path)

        assert diff.introduced_project_failures() == ["proj"]

    def test_comment_title_for_new_non_panic_crash(self):
        """A new abnormal exit without a panic message (e.g. stack overflow)
        should produce a scary title, not the default neutral one."""
        old_data = {"outputs": [_make_output("proj", [], time_s=1.5, return_code=0)]}
        new_data = {"outputs": [_make_output("proj", [], time_s=None, return_code=-11)]}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump(old_data, f1)
            old_path = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump(new_data, f2)
            new_path = f2.name

        diff = DiagnosticDiff(old_path, new_path)

        assert diff.generate_comment_title() == (
            "## `ecosystem-analyzer` results: new crashes detected ❌"
        )

    def test_comment_title_combines_panics_crashes_and_timeouts(self):
        old_data = {
            "outputs": [
                _make_output("panic_proj", [], time_s=1.5, return_code=0),
                _make_output("crash_proj", [], time_s=1.5, return_code=0),
                _make_output("timeout_proj", [], time_s=1.5, return_code=0),
            ]
        }
        new_data = {
            "outputs": [
                _make_output(
                    "panic_proj",
                    [],
                    panic_messages=["thread 'main' panicked at new panic"],
                    time_s=None,
                    return_code=101,
                ),
                _make_output("crash_proj", [], time_s=None, return_code=-11),
                _make_output("timeout_proj", [], time_s=None, return_code=None),
            ]
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump(old_data, f1)
            old_path = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump(new_data, f2)
            new_path = f2.name

        diff = DiagnosticDiff(old_path, new_path)
        assert diff.generate_comment_title() == (
            "## `ecosystem-analyzer` results: new crashes detected ❌"
        )

    def test_comment_title_for_only_new_timeouts(self):
        """When every new failure is a timeout, say so — calling them
        'crashes' would be misleading."""
        old_data = {
            "outputs": [
                _make_output("a", [], time_s=1.5, return_code=0),
                _make_output("b", [], time_s=1.5, return_code=0),
            ]
        }
        new_data = {
            "outputs": [
                _make_output("a", [], time_s=None, return_code=None),
                _make_output("b", [], time_s=None, return_code=None),
            ]
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump(old_data, f1)
            old_path = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump(new_data, f2)
            new_path = f2.name

        diff = DiagnosticDiff(old_path, new_path)
        assert diff.generate_comment_title() == (
            "## `ecosystem-analyzer` results: new timeouts detected ❌"
        )

    def test_partial_panic_fix_not_celebrated(self):
        """A project that loses some (but not all) panics while still
        failing is *not* celebrated in the PR title or summary table —
        the partial improvement is only visible in the raw diff and HTML
        report."""
        shared = "thread 'main' panicked at shared"
        old_only = "thread 'main' panicked at old-only"

        old_data = {
            "outputs": [
                _make_output(
                    "partial",
                    [],
                    panic_messages=[shared, old_only],
                    time_s=None,
                    return_code=101,
                )
            ]
        }
        new_data = {
            "outputs": [
                _make_output(
                    "partial",
                    [],
                    panic_messages=[shared],
                    time_s=None,
                    return_code=101,
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

        entry = next(
            e for e in diff.diffs["failed_projects"] if e["project"] == "partial"
        )
        assert entry["failure_status"] == "persistent"
        assert entry["fixed_panic_messages"] == [old_only]

        assert not diff.has_fixed_failures()
        # Plain title — no celebration.
        assert diff.generate_comment_title() == "## `ecosystem-analyzer` results"

        markdown = diff.render_statistics_markdown()
        # Hidden from the PR-comment summary table.
        assert "**Failing projects**:" not in markdown
        # Still surfaced in the raw diff so reviewers see the improvement.
        assert "PARTIAL FIX" in markdown
        assert "partial" in markdown

    def test_disjoint_panics_on_still_failing_project_counts_as_new(self):
        """A project failing with panic A, then failing with a completely
        different panic B, is a regression — the introduced panic wins over
        the fact that the old panic went away."""
        old_panic = "thread 'main' panicked at old site"
        new_panic = "thread 'main' panicked at new site"

        old_data = {
            "outputs": [
                _make_output(
                    "swapped",
                    [],
                    panic_messages=[old_panic],
                    time_s=None,
                    return_code=101,
                )
            ]
        }
        new_data = {
            "outputs": [
                _make_output(
                    "swapped",
                    [],
                    panic_messages=[new_panic],
                    time_s=None,
                    return_code=101,
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
        entry = next(
            e for e in diff.diffs["failed_projects"] if e["project"] == "swapped"
        )
        assert entry["failure_status"] == "new"
        assert entry["introduced_panic_messages"] == [new_panic]
        assert entry["fixed_panic_messages"] == [old_panic]
        assert entry["persistent_panic_messages"] == []

        assert diff.has_new_failures()
        # `has_fixed_failures` only fires on full recovery, not on panic swaps.
        assert not diff.has_fixed_failures()
        assert "new crashes detected" in diff.generate_comment_title()

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

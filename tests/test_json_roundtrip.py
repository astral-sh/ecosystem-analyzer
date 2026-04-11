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

    def test_flaky_added_excluded_from_statistics(self):
        """A new flaky location is excluded from statistics."""
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
                "b.py",
                10,
                1,
                [
                    _make_variant("b.py", 10, 1, "variant A", count=2),
                    _make_variant("b.py", 10, 1, "variant B", count=1),
                ],
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
        stats = diff._calculate_statistics()
        assert stats["total_added"] == 0  # Flaky location excluded from stats

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

    def test_flaky_genuinely_new_location_excluded_from_statistics(self):
        """A genuinely new flaky location is excluded from statistics."""
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
                "c.py",
                50,
                1,
                [
                    _make_variant("c.py", 50, 1, "new variant", count=2),
                ],
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
        stats = diff._calculate_statistics()
        assert stats["total_added"] == 0

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
        new_data = {"outputs": [_make_output("proj", [], time_s=None, return_code=None)]}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump(old_data, f1)
            old_path = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump(new_data, f2)
            new_path = f2.name

        diff = DiagnosticDiff(old_path, new_path)

        assert diff.introduced_project_failures() == ["proj"]

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
        new_data = {
            "outputs": [_make_output("proj", [diag, new_diag], flaky_runs=5)]
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f1:
            json.dump(old_data, f1)
            old_path = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f2:
            json.dump(new_data, f2)
            new_path = f2.name

        diff = DiagnosticDiff(old_path, new_path)
        stats = diff._calculate_statistics()
        assert stats["total_added"] == 1

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

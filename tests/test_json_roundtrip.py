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
        new_data = {
            "outputs": [_make_output("proj", [diag], new_flaky, flaky_runs=3)]
        }

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

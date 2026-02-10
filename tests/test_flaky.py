from ecosystem_analyzer.diagnostic import Diagnostic
from ecosystem_analyzer.flaky import classify_diagnostics


def _diag(
    path: str,
    line: int,
    column: int,
    message: str,
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


class TestClassifyDiagnostics:
    def test_all_stable(self):
        """When all runs produce identical diagnostics, everything is stable."""
        d1 = _diag("a.py", 1, 1, "msg1")
        d2 = _diag("a.py", 2, 1, "msg2")

        stable, flaky = classify_diagnostics(
            [
                [d1, d2],
                [d1, d2],
                [d1, d2],
            ]
        )

        assert len(stable) == 2
        assert len(flaky) == 0

    def test_all_flaky(self):
        """Diagnostics that appear in only some runs are flaky."""
        d1 = _diag("a.py", 1, 1, "msg1")
        d2 = _diag("a.py", 1, 1, "msg2")

        stable, flaky = classify_diagnostics(
            [
                [d1],
                [d2],
            ]
        )

        assert len(stable) == 0
        assert len(flaky) == 1
        # Both variants at the same location (same column)
        assert flaky[0]["path"] == "a.py"
        assert flaky[0]["line"] == 1
        assert flaky[0]["column"] == 1
        assert len(flaky[0]["variants"]) == 2
        # Each appeared in 1 of 2 runs
        for v in flaky[0]["variants"]:
            assert v["count"] == 1

    def test_mixed_stable_and_flaky(self):
        """Some diagnostics are stable, others are flaky."""
        stable_diag = _diag("a.py", 1, 1, "always here")
        flaky_diag = _diag("a.py", 5, 1, "sometimes here")

        stable, flaky = classify_diagnostics(
            [
                [stable_diag, flaky_diag],
                [stable_diag],
                [stable_diag],
            ]
        )

        assert len(stable) == 1
        assert stable[0]["message"] == "always here"
        assert len(flaky) == 1
        assert flaky[0]["line"] == 5
        assert len(flaky[0]["variants"]) == 1
        assert flaky[0]["variants"][0]["count"] == 1
        assert flaky[0]["variants"][0]["diagnostic"]["message"] == "sometimes here"

    def test_flaky_counts(self):
        """Variant counts reflect how many runs each appeared in."""
        d1 = _diag("a.py", 10, 1, "variant A")
        d2 = _diag("a.py", 10, 1, "variant B")

        stable, flaky = classify_diagnostics(
            [
                [d1],
                [d1],
                [d2],
            ]
        )

        assert len(flaky) == 1
        variants = {
            v["diagnostic"]["message"]: v["count"] for v in flaky[0]["variants"]
        }
        assert variants["variant A"] == 2
        assert variants["variant B"] == 1

    def test_flaky_grouped_by_location(self):
        """Flaky diagnostics at the same file+line+column are grouped together."""
        d1 = _diag("a.py", 10, 1, "variant A")
        d2 = _diag("a.py", 10, 1, "variant B")
        d3 = _diag("b.py", 20, 1, "other file")

        stable, flaky = classify_diagnostics(
            [
                [d1, d3],
                [d2],
            ]
        )

        assert len(stable) == 0
        assert len(flaky) == 2

        # First location: a.py:10:1
        a_loc = next(f for f in flaky if f["path"] == "a.py")
        assert a_loc["line"] == 10
        assert a_loc["column"] == 1
        assert len(a_loc["variants"]) == 2

        # Second location: b.py:20:1
        b_loc = next(f for f in flaky if f["path"] == "b.py")
        assert b_loc["line"] == 20
        assert len(b_loc["variants"]) == 1

    def test_flaky_different_columns_separate_locations(self):
        """Flaky diagnostics at the same line but different columns are separate."""
        d1 = _diag("a.py", 10, 1, "msg")
        d2 = _diag("a.py", 10, 5, "msg")

        stable, flaky = classify_diagnostics(
            [
                [d1],
                [d2],
            ]
        )

        assert len(flaky) == 2
        assert flaky[0]["column"] == 1
        assert flaky[1]["column"] == 5

    def test_deduplication_within_run(self):
        """Duplicate diagnostics within a single run don't inflate counts."""
        d = _diag("a.py", 1, 1, "msg")

        stable, flaky = classify_diagnostics(
            [
                [d, d, d],
                [d],
            ]
        )

        assert len(stable) == 1
        assert len(flaky) == 0

    def test_two_runs_minimum(self):
        """Two runs is the minimum for flaky detection."""
        d1 = _diag("a.py", 1, 1, "stable")
        d2 = _diag("a.py", 2, 1, "flaky")

        stable, flaky = classify_diagnostics(
            [
                [d1, d2],
                [d1],
            ]
        )

        assert len(stable) == 1
        assert len(flaky) == 1

    def test_stable_sorted_by_path_line_column(self):
        """Stable diagnostics are sorted by path, line, column, message."""
        d1 = _diag("b.py", 1, 1, "msg")
        d2 = _diag("a.py", 2, 1, "msg")
        d3 = _diag("a.py", 1, 5, "msg")
        d4 = _diag("a.py", 1, 1, "msg")

        stable, _ = classify_diagnostics(
            [
                [d1, d2, d3, d4],
                [d1, d2, d3, d4],
            ]
        )

        assert (
            stable[0]["path"] == "a.py"
            and stable[0]["line"] == 1
            and stable[0]["column"] == 1
        )
        assert (
            stable[1]["path"] == "a.py"
            and stable[1]["line"] == 1
            and stable[1]["column"] == 5
        )
        assert stable[2]["path"] == "a.py" and stable[2]["line"] == 2
        assert stable[3]["path"] == "b.py"

    def test_flaky_locations_sorted(self):
        """Flaky locations are sorted by (path, line, column)."""
        d1 = _diag("b.py", 10, 1, "msg")
        d2 = _diag("a.py", 20, 1, "msg")
        d3 = _diag("a.py", 5, 1, "msg")
        d4 = _diag("a.py", 5, 10, "msg")

        _, flaky = classify_diagnostics(
            [
                [d1, d2, d3, d4],
                [],
            ]
        )

        assert (
            flaky[0]["path"] == "a.py"
            and flaky[0]["line"] == 5
            and flaky[0]["column"] == 1
        )
        assert (
            flaky[1]["path"] == "a.py"
            and flaky[1]["line"] == 5
            and flaky[1]["column"] == 10
        )
        assert flaky[2]["path"] == "a.py" and flaky[2]["line"] == 20
        assert flaky[3]["path"] == "b.py" and flaky[3]["line"] == 10

    def test_flaky_variants_sorted_by_lint_name_message(self):
        """Variants within a flaky location are sorted by lint_name, message."""
        d1 = _diag("a.py", 10, 1, "msg B", lint_name="z-lint")
        d2 = _diag("a.py", 10, 1, "msg A", lint_name="a-lint")

        _, flaky = classify_diagnostics(
            [
                [d1],
                [d2],
            ]
        )

        assert len(flaky) == 1
        assert flaky[0]["variants"][0]["diagnostic"]["lint_name"] == "a-lint"
        assert flaky[0]["variants"][1]["diagnostic"]["lint_name"] == "z-lint"

    def test_empty_runs(self):
        """Runs with no diagnostics produce no stable or flaky results."""
        stable, flaky = classify_diagnostics(
            [
                [],
                [],
            ]
        )

        assert len(stable) == 0
        assert len(flaky) == 0

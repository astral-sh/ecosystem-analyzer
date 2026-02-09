"""Logic for detecting flaky diagnostics by comparing multiple ty runs."""

from .diagnostic import Diagnostic
from .run_output import FlakyLocation, FlakyVariant


def _diagnostic_key(diag: Diagnostic) -> tuple[str, int, int, str, str, str]:
    """Return a hashable key that uniquely identifies a diagnostic."""
    return (
        diag["path"],
        diag["line"],
        diag["column"],
        diag["level"],
        diag["lint_name"],
        diag["message"],
    )


def _location_key(diag: Diagnostic) -> tuple[str, int, int]:
    """Return a (path, line, column) key for grouping flaky diagnostics."""
    return (diag["path"], diag["line"], diag["column"])


def classify_diagnostics(
    all_runs: list[list[Diagnostic]],
) -> tuple[list[Diagnostic], list[FlakyLocation]]:
    """Classify diagnostics from multiple runs as stable or flaky.

    A diagnostic is "stable" if it appears in ALL runs (by exact key match).
    All other diagnostics are "flaky" and grouped by (path, line).

    Each flaky variant records how many runs it appeared in.

    Returns (stable_diagnostics, flaky_locations).
    """
    n = len(all_runs)
    assert n >= 2, "Need at least 2 runs to detect flakiness"

    # Count how many runs each diagnostic key appears in
    key_counts: dict[tuple[str, int, int, str, str, str], int] = {}
    # Keep one representative Diagnostic for each key
    key_to_diag: dict[tuple[str, int, int, str, str, str], Diagnostic] = {}

    for run_diagnostics in all_runs:
        # Deduplicate within a single run — a key counts once per run
        seen_in_run: set[tuple[str, int, int, str, str, str]] = set()
        for diag in run_diagnostics:
            key = _diagnostic_key(diag)
            if key not in seen_in_run:
                seen_in_run.add(key)
                key_counts[key] = key_counts.get(key, 0) + 1
                if key not in key_to_diag:
                    key_to_diag[key] = diag

    # Partition into stable and flaky
    stable: list[Diagnostic] = []
    flaky_by_location: dict[tuple[str, int, int], list[FlakyVariant]] = {}

    for key, count in key_counts.items():
        diag = key_to_diag[key]
        if count == n:
            stable.append(diag)
        else:
            loc = _location_key(diag)
            if loc not in flaky_by_location:
                flaky_by_location[loc] = []
            flaky_by_location[loc].append(
                FlakyVariant(diagnostic=diag, count=count)
            )

    # Sort stable diagnostics by path, line, column, message
    stable.sort(
        key=lambda d: (d["path"], d["line"], d["column"], d["message"]),
    )

    # Build sorted FlakyLocation list
    flaky_locations: list[FlakyLocation] = []
    for (path, line, column) in sorted(flaky_by_location.keys()):
        variants = flaky_by_location[(path, line, column)]
        # Sort variants by lint_name, message
        variants.sort(
            key=lambda v: (
                v["diagnostic"]["lint_name"],
                v["diagnostic"]["message"],
            )
        )
        flaky_locations.append(
            FlakyLocation(path=path, line=line, column=column, variants=variants)
        )

    return stable, flaky_locations

"""Logic for detecting flaky diagnostics by comparing multiple ty runs."""

from collections import Counter

from .schema import (
    Diagnostic,
    DiagnosticKey,
    FlakyLocation,
    FlakyVariant,
    SourceLocationKey,
)


def _diagnostic_key(diag: Diagnostic) -> DiagnosticKey:
    """Return a hashable key that uniquely identifies a diagnostic."""
    return (
        diag["path"],
        diag["line"],
        diag["column"],
        diag["level"],
        diag["lint_name"],
        diag["message"],
    )


def _location_key(diag: Diagnostic) -> SourceLocationKey:
    """Return a (path, line, column) key for grouping flaky diagnostics."""
    return (diag["path"], diag["line"], diag["column"])


def classify_diagnostics(
    all_runs: list[list[Diagnostic]],
) -> tuple[list[Diagnostic], list[FlakyLocation]]:
    """Classify diagnostics from multiple runs as stable or flaky.

    A diagnostic occurrence is "stable" if it appears in ALL runs.
    Repeated stable diagnostics are preserved, while intermittent duplicate
    occurrences are "flaky" and grouped by (path, line).

    Each flaky variant records how many runs it appeared in.

    Returns (stable_diagnostics, flaky_locations).
    """
    n = len(all_runs)
    assert n >= 2, "Need at least 2 runs to detect flakiness"

    # Include occurrence indexes so duplicate diagnostics count independently.
    occurrence_counts: Counter[tuple[DiagnosticKey, int]] = Counter()
    # Keep one representative Diagnostic for each key
    key_to_diag: dict[DiagnosticKey, Diagnostic] = {}

    for run_diagnostics in all_runs:
        occurrences_in_run: Counter[DiagnosticKey] = Counter()
        for diag in run_diagnostics:
            key = _diagnostic_key(diag)
            occurrence = occurrences_in_run[key]
            occurrences_in_run[key] += 1
            occurrence_counts[key, occurrence] += 1
            if key not in key_to_diag:
                key_to_diag[key] = diag

    # Partition into stable and flaky
    stable: list[Diagnostic] = []
    flaky_by_location: dict[SourceLocationKey, list[FlakyVariant]] = {}

    for (key, _occurrence), count in occurrence_counts.items():
        diag = key_to_diag[key]
        if count == n:
            stable.append(diag)
        else:
            loc = _location_key(diag)
            if loc not in flaky_by_location:
                flaky_by_location[loc] = []
            flaky_by_location[loc].append(FlakyVariant(diagnostic=diag, count=count))

    # Sort stable diagnostics by path, line, column, message
    stable.sort(
        key=lambda d: (d["path"], d["line"], d["column"], d["message"]),
    )

    # Build sorted FlakyLocation list
    flaky_locations: list[FlakyLocation] = []
    for path, line, column in sorted(flaky_by_location.keys()):
        variants = flaky_by_location[path, line, column]
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

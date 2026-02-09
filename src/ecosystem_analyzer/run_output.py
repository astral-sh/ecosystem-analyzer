from typing import NotRequired, TypedDict

from .diagnostic import Diagnostic


class FlakyVariant(TypedDict):
    """A diagnostic variant seen at a flaky location, with its frequency."""

    diagnostic: Diagnostic
    count: int  # How many runs this variant appeared in


class FlakyLocation(TypedDict):
    """A source location where diagnostics vary between runs."""

    path: str
    line: int
    column: int
    variants: list[FlakyVariant]


class RunOutput(TypedDict):
    project: str
    project_location: str
    ty_commit: str
    diagnostics: list[Diagnostic]
    flaky_diagnostics: NotRequired[list[FlakyLocation]]
    flaky_runs: NotRequired[int]  # Total number of runs used for flaky detection
    time_s: float | None
    return_code: int | None

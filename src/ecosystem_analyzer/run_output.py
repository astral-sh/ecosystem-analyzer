from typing import NotRequired, TypedDict

from .diagnostic import Diagnostic


class ProjectMetadata(TypedDict):
    kind: NotRequired[str]


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


class OutputVariant(TypedDict):
    """Output text seen in some or all runs, with its frequency."""

    message: str
    count: int


class ExitStatus(TypedDict):
    """An exit status observed across one or more runs."""

    return_code: int | None
    count: int
    panic_messages: NotRequired[list[OutputVariant]]
    stderr: NotRequired[list[OutputVariant]]


class RunOutput(TypedDict):
    project: str
    project_location: str
    ty_commit: str
    diagnostics: list[Diagnostic]
    flaky_diagnostics: NotRequired[list[FlakyLocation]]
    exit_statuses: list[ExitStatus]
    flaky_runs: NotRequired[int]  # Total number of runs used for flaky detection
    median_time_s: float | None
    project_metadata: NotRequired[ProjectMetadata]

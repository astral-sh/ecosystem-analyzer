"""Shared typed schemas for ecosystem runs, diagnostic diffs, and reports."""

from pathlib import Path
from typing import Literal, NotRequired

from git import Repo
from typing_extensions import TypedDict

type DiagnosticLevel = Literal["error", "warning", "fatal"]
type SourceLocationKey = tuple[str, int, int]
type DiagnosticContentKey = tuple[DiagnosticLevel, str, str]
type DiagnosticKey = tuple[str, int, int, DiagnosticLevel, str, str]
type ProjectStatus = Literal["success", "timeout", "abnormal exit", "flaky"]
type FailureStatus = Literal[
    "new", "new_panics", "changed", "persistent", "reduced", "fixed"
]
type TimingFailure = Literal["both_failed", "old_failed", "new_failed"]


class SourceLocation(TypedDict):
    """The file path, line, and column associated with a diagnostic."""

    path: str
    line: int
    column: int


class Diagnostic(SourceLocation):
    """A ty diagnostic and its optional source-code link."""

    level: DiagnosticLevel
    lint_name: str
    message: str
    github_ref: NotRequired[str]


class ProjectMetadata(TypedDict, closed=True):
    """Optional classification metadata attached to an analyzed project."""

    kind: str


class ProjectIdentity(TypedDict):
    """The project name and optional metadata shared by run and report records."""

    project: str
    project_metadata: NotRequired[ProjectMetadata]
    strict_settings: bool


class ProjectInfo(ProjectIdentity):
    """Project identity, repository location, and optional metadata."""

    project_location: str


class FlakyVariant(TypedDict, closed=True):
    """A diagnostic variant seen at a flaky location, with its frequency."""

    diagnostic: Diagnostic
    count: int  # How many runs this variant appeared in


class FlakyLocation(SourceLocation):
    """A source location where diagnostics vary between runs."""

    variants: list[FlakyVariant]


class OutputVariant(TypedDict, closed=True):
    """Output text seen in some or all runs, with its frequency."""

    message: str
    count: int


class ExitStatus(TypedDict, closed=True):
    """An exit status observed across one or more runs."""

    return_code: int | None
    count: int
    panic_messages: list[OutputVariant]
    stderr: NotRequired[list[OutputVariant]]


class RunOutput(ProjectIdentity, closed=True):
    """Diagnostics, exit evidence, and timing collected for one project."""

    project_location: NotRequired[str]
    ty_commit: NotRequired[str]
    diagnostics: list[Diagnostic]
    flaky_diagnostics: list[FlakyLocation]
    exit_statuses: list[ExitStatus]
    flaky_runs: int
    """Total number of runs used for flaky detection"""
    median_time_s: float | None


class RunData(TypedDict, closed=True):
    """The top-level JSON payload containing ecosystem project results."""

    outputs: list[RunOutput]


class CliContext(TypedDict, closed=True):
    """Shared Click context passed from the root command to its subcommands."""

    repository: Repo | None
    target: Path | None
    verbose: bool
    flaky_runs: int


class CommitStatistics(TypedDict, closed=True):
    """The commit identity and diagnostic count recorded by a history run."""

    commit: str
    commit_message: str
    total_diagnostics: int


class HistoryData(TypedDict, closed=True):
    """The top-level JSON payload containing per-commit history statistics."""

    statistics: list[CommitStatistics]


class ReportDiagnostic(Diagnostic, ProjectInfo, closed=True):
    """A project-qualified diagnostic prepared for HTML report rendering."""

    is_flaky: bool
    flaky_runs: int
    variants: list[FlakyVariant]


class DiagnosticTextDiff(TypedDict, closed=True):
    """The old diagnostic, new diagnostic, and their rendered text diff."""

    old: Diagnostic
    new: Diagnostic
    diff: list[str]


class DiagnosticLine(TypedDict, closed=True):
    """Diagnostics associated with one added or removed source line."""

    line: int
    diagnostics: list[Diagnostic]


class ModifiedDiagnosticLine(TypedDict, closed=True):
    """Added, removed, and rewritten diagnostics on one source line."""

    line: int
    removed: list[Diagnostic]
    added: list[Diagnostic]
    text_diffs: list[DiagnosticTextDiff]


class LineDiffData(TypedDict, closed=True):
    """Added, removed, and modified diagnostic lines within one file."""

    added_lines: list[DiagnosticLine]
    removed_lines: list[DiagnosticLine]
    modified_lines: list[ModifiedDiagnosticLine]


class DiagnosticFile(TypedDict, closed=True):
    """Diagnostics belonging to one added or removed source file."""

    path: str
    diagnostics: list[Diagnostic]


class ModifiedDiagnosticFile(TypedDict, closed=True):
    """Line-level diagnostic differences within one modified file."""

    path: str
    diffs: LineDiffData


class FileDiffData(TypedDict, closed=True):
    """Added, removed, and modified files within one analyzed project."""

    added_files: list[DiagnosticFile]
    removed_files: list[DiagnosticFile]
    modified_files: list[ModifiedDiagnosticFile]


class AnnotatedFlakyLocation(FlakyLocation, closed=True):
    """A flaky source location annotated with its observed run count."""

    flaky_runs: int


class ChangedFlakyLocation(TypedDict, closed=True):
    """The old and new variants observed at a changed flaky location."""

    old: AnnotatedFlakyLocation
    new: AnnotatedFlakyLocation


class FlakyDiagnosticDiffData(TypedDict, closed=True):
    """Added, removed, and changed flaky diagnostic locations."""

    added: list[AnnotatedFlakyLocation]
    removed: list[AnnotatedFlakyLocation]
    changed: list[ChangedFlakyLocation]


class AddedOrRemovedProjectDiff(ProjectInfo, closed=True):
    """Diagnostics and exit evidence for an added or removed project."""

    diagnostics: list[Diagnostic]
    exit_statuses: list[ExitStatus]
    exit_status_runs: int
    flaky_diagnostics: list[FlakyLocation]
    flaky_runs: int


class ModifiedProjectDiff(ProjectInfo, closed=True):
    """Stable and flaky diagnostic changes within an existing project."""

    diffs: FileDiffData
    flaky_diffs: NotRequired[FlakyDiagnosticDiffData]
    flaky_file_diffs: NotRequired[dict[str, FlakyDiagnosticDiffData]]


class FailedProjectDiff(ProjectInfo, closed=True):
    """Baseline and candidate failure evidence for one analyzed project."""

    old_status: ProjectStatus
    new_status: ProjectStatus
    old_exit_statuses: list[ExitStatus]
    new_exit_statuses: list[ExitStatus]
    old_runs: int
    new_runs: int
    old_panic_messages: list[str]
    new_panic_messages: list[str]
    introduced_panic_messages: list[str]
    fixed_panic_messages: list[str]
    persistent_panic_messages: list[str]
    old_persistent_panic_messages: list[str]
    new_persistent_panic_messages: list[str]
    failure_status: FailureStatus


class ExitStatusDiff(TypedDict):
    """Old and new exit-status observations and their run counts."""

    old: list[ExitStatus]
    new: list[ExitStatus]
    old_runs: int
    new_runs: int


class FlakyExitStatusChange(ExitStatusDiff, ProjectInfo, closed=True):
    """Project-qualified changes to intermittent exit-status evidence."""


class DiagnosticDiffData(TypedDict, closed=True):
    """Project-level differences between two ecosystem analyzer runs."""

    added_projects: list[AddedOrRemovedProjectDiff]
    removed_projects: list[AddedOrRemovedProjectDiff]
    modified_projects: list[ModifiedProjectDiff]
    failed_projects: list[FailedProjectDiff]
    flaky_exit_status_changes: list[FlakyExitStatusChange]


class MergedChangeStats(TypedDict):
    """Counts shared by per-project and per-lint diagnostic summaries."""

    added: int
    removed: int
    changed: int
    net_change: int
    total_change: int


class MergedLintStats(MergedChangeStats, closed=True):
    """Statistics for a single lint rule in the merged view."""

    lint_name: str


class MergedProjectStats(MergedChangeStats, closed=True):
    """Statistics for a single project in the merged view."""

    project_name: str
    is_flaky: bool


class DiffStatistics(TypedDict, closed=True):
    """Statistics about diagnostic changes."""

    total_added: int
    total_removed: int
    total_changed: int
    failed_projects: int
    merged_by_lint: list[MergedLintStats]
    merged_by_project: list[MergedProjectStats]


class TimingComparison(TypedDict, closed=True):
    """Runtime and failure comparison data for one analyzed project."""

    project: str
    old_time: float | None
    new_time: float | None
    factor: float
    is_failed: bool
    failure_type: TimingFailure | None
    old_is_timeout: bool
    new_is_timeout: bool
    old_is_abnormal: bool
    new_is_abnormal: bool


class TimingSummary(TypedDict, closed=True):
    """Aggregate speedup, slowdown, and failure counts for a timing report."""

    speedups: int
    slowdowns: int
    timeouts: int
    abnormal_exits: int
    avg_factor: float


class LargeTimingChange(TypedDict, closed=True):
    """A project whose runtime changed beyond the report threshold."""

    project: str
    old_time: float
    new_time: float
    factor: float
    change_percent: float

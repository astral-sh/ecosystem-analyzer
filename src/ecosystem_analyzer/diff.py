import difflib
import json
import os
import random
from collections import Counter
from enum import Enum
from itertools import chain
from pathlib import Path
from typing import Any, Literal, TypedDict

from jinja2 import Environment, FileSystemLoader, PackageLoader

from .diagnostic import Diagnostic, index_panic_messages, normalize_stderr
from .run_output import ExitStatus, OutputVariant, RunOutput


class JsonData(TypedDict):
    outputs: list[RunOutput]


class MergedLintStats(TypedDict):
    """Statistics for a single lint rule in the merged view."""

    lint_name: str
    added: int
    removed: int
    changed: int
    net_change: int
    total_change: int


class MergedProjectStats(TypedDict):
    """Statistics for a single project in the merged view."""

    project_name: str
    added: int
    removed: int
    changed: int
    net_change: int
    total_change: int
    is_flaky: bool


class DiffStatistics(TypedDict):
    """Statistics about diagnostic changes."""

    total_added: int
    total_removed: int
    total_changed: int
    failed_projects: int
    merged_by_lint: list[MergedLintStats]
    merged_by_project: list[MergedProjectStats]


_FAILURE_STATUS_LABELS = {
    "new": "❌ newly failing",
    "new_panics": "❌ new panics",
    "changed": "➖ failure mode changed",
    "persistent": "➖ persistent",
    "reduced": "🎉 panics reduced",
    "fixed": "🎉 crashes fixed",
}

_FAILURE_STATUS_TITLES = {
    "new": "Failure introduced by this PR",
    "new_panics": "New panic messages introduced by this PR while the project remains failing",
    "changed": "Failure mode changed between the baseline and PR",
    "persistent": "Same failure on both baseline and PR",
    "reduced": "Some panic messages that existed on the baseline are no longer present",
    "fixed": "Failure that existed on the baseline is no longer present",
}


class _ProjectStatus(Enum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    ABNORMAL_EXIT = "abnormal exit"
    FLAKY = "flaky"


def _compare_panic_messages(
    old_messages: list[str], new_messages: list[str]
) -> tuple[list[str], list[str], list[str]]:
    """Return introduced, fixed, and persistent panic messages."""
    old_messages_by_key = index_panic_messages(old_messages)
    new_messages_by_key = index_panic_messages(new_messages)
    introduced = []
    fixed = []
    persistent = []

    for key in old_messages_by_key.keys() | new_messages_by_key.keys():
        old_counts = Counter(old_messages_by_key.get(key, []))
        new_counts = Counter(new_messages_by_key.get(key, []))

        # Match unchanged raw messages first so any count delta reports the
        # most likely added or removed panic site.
        exact_matches = old_counts & new_counts
        unmatched_old = list((old_counts - exact_matches).elements())
        unmatched_new = list((new_counts - exact_matches).elements())
        persistent.extend(exact_matches.elements())

        persistent_count = min(len(unmatched_old), len(unmatched_new))
        persistent.extend(unmatched_new[:persistent_count])
        fixed.extend(unmatched_old[persistent_count:])
        introduced.extend(unmatched_new[persistent_count:])

    return sorted(introduced), sorted(fixed), sorted(persistent)


def _failure_descriptor(
    project: dict[str, Any], direction: Literal["new", "fixed"]
) -> str:
    """Describe the kind of failure for `direction` ('new' or 'fixed')."""
    status_key = "new_status" if direction == "new" else "old_status"
    status = project[status_key]
    panic_messages_key = (
        "introduced_panic_messages" if direction == "new" else "fixed_panic_messages"
    )
    if project[panic_messages_key]:
        return "panic"
    if status == "timeout":
        return "timeout"
    if status == "abnormal exit":
        return "crash"
    return "failure"


class DiagnosticDiff:
    """Class for comparing diagnostic data between two JSON files."""

    RAW_DIFF_SAMPLE_SEED = 137
    LARGE_TIMING_CHANGE_THRESHOLD = 0.5

    # GitHub's comment body limit is 65,536 characters. We keep a small
    # margin so surrounding markup (details/summary tags, etc.) and any
    # future additions don't push us over. This margin is effectively a
    # safety buffer _on top_ of the calculated size of the summary table.
    GITHUB_COMMENT_CHAR_LIMIT = 65_536
    GITHUB_COMMENT_CHAR_MARGIN = 1_024

    def __init__(
        self,
        old_file: str,
        new_file: str,
        old_name: str | None = None,
        new_name: str | None = None,
    ):
        """Initialize with paths to the old and new JSON files."""
        self.old_file = old_file
        self.new_file = new_file
        self.ty_repo_url = "https://github.com/astral-sh/ruff"
        self.old_data: JsonData = self._load_json(old_file)
        self.new_data: JsonData = self._load_json(new_file)

        self.old_commit = self._get_commit(self.old_data)
        self.new_commit = self._get_commit(self.new_data)

        # Use provided names or fallback to commit hashes
        self.old_branch_info = old_name or self.old_commit[:7]
        self.new_branch_info = new_name or self.new_commit[:7]

        self.old_diagnostics = self._count_diagnostics(self.old_data)
        self.new_diagnostics = self._count_diagnostics(self.new_data)

        self.diffs = self._compute_diffs()

    def _load_json(self, file_path: str) -> JsonData:
        """Load and parse a JSON file."""
        with open(file_path) as f:
            data = json.load(f)

        return data

    def _get_commit(self, data: JsonData) -> str:
        ty_commits = {
            output.get("ty_commit", "unknown")
            for output in data["outputs"]
            if output.get("ty_commit") is not None
        }

        # If no ty_commit fields are present, return "unknown"
        if not ty_commits:
            return "unknown"

        # If all commits are the same (or there's only one), return it
        if len(ty_commits) == 1:
            return ty_commits.pop()

        # If there are multiple different commits, that's an error
        if len(ty_commits) > 1 and "unknown" in ty_commits:
            # Remove "unknown" and check again
            ty_commits.discard("unknown")
            if len(ty_commits) == 1:
                return ty_commits.pop()

        raise RuntimeError(
            "Error: The JSON file must contain diagnostics from a single ty commit."
        )

    def _all_diagnostic_locations(self, project: RunOutput) -> set[tuple]:
        """Build a set of (path, line, column) locations from all diagnostics.

        Includes locations from both stable diagnostics and flaky variants.
        """
        locs: set[tuple] = set()
        for d in project.get("diagnostics", []):
            locs.add((d["path"], d["line"], d["column"]))
        for loc in project.get("flaky_diagnostics", []):
            locs.add((loc["path"], loc["line"], loc["column"]))
        return locs

    def _exclude_known_overlaps(
        self, flaky_locations: list, other_all_locations: set[tuple]
    ) -> list:
        """Remove flaky locations that exist at a known location on the other side.

        Due to statistical noise with limited runs, a flaky location might
        have entirely different variants in one batch vs another.  If the
        *location* (path, line, column) has any diagnostic on the other side
        (whether stable or flaky), we consider the whole location accounted
        for — it's the same nondeterministic source location, just with
        different luck in which variants showed up.
        """
        if not other_all_locations:
            return flaky_locations

        return [
            loc
            for loc in flaky_locations
            if (loc["path"], loc["line"], loc["column"]) not in other_all_locations
        ]

    def _flaky_variant_key(self, variant: dict) -> tuple:
        """Hashable key for a single flaky variant."""
        d = variant["diagnostic"]
        return (d["level"], d["lint_name"], d["message"])

    def _flaky_location_variant_set(self, location: dict) -> frozenset:
        """Frozenset of variant keys for a flaky location."""
        return frozenset(self._flaky_variant_key(v) for v in location["variants"])

    def _compare_flaky_locations(
        self,
        old_flaky: list,
        new_flaky: list,
        old_flaky_runs: int | None,
        new_flaky_runs: int | None,
    ) -> dict[str, list]:
        """Compare flaky locations between old and new.

        Returns dict with added/removed/changed lists.
        Each flaky location is annotated with its flaky_runs count.
        """
        result: dict[str, list] = {"added": [], "removed": [], "changed": []}

        old_by_loc = {
            (loc["path"], loc["line"], loc["column"]): loc for loc in old_flaky
        }
        new_by_loc = {
            (loc["path"], loc["line"], loc["column"]): loc for loc in new_flaky
        }

        old_keys = set(old_by_loc.keys())
        new_keys = set(new_by_loc.keys())

        for key in sorted(new_keys - old_keys):
            loc = dict(new_by_loc[key])
            loc["flaky_runs"] = new_flaky_runs
            result["added"].append(loc)

        for key in sorted(old_keys - new_keys):
            loc = dict(old_by_loc[key])
            loc["flaky_runs"] = old_flaky_runs
            result["removed"].append(loc)

        for key in sorted(old_keys & new_keys):
            old_loc = old_by_loc[key]
            new_loc = new_by_loc[key]
            if self._flaky_location_variant_set(
                old_loc
            ) != self._flaky_location_variant_set(new_loc):
                old_annotated = dict(old_loc)
                old_annotated["flaky_runs"] = old_flaky_runs
                new_annotated = dict(new_loc)
                new_annotated["flaky_runs"] = new_flaky_runs
                result["changed"].append({"old": old_annotated, "new": new_annotated})

        return result

    def _organize_flaky_diffs_by_file(
        self, flaky_diffs: dict[str, list]
    ) -> dict[str, dict[str, list]]:
        """Organize flaky diffs by file path for inline rendering.

        Returns {path: {"added": [...], "removed": [...], "changed": [...]}}.
        """
        by_file: dict[str, dict[str, list]] = {}

        for change_type in ("added", "removed", "changed"):
            for item in flaky_diffs[change_type]:
                if change_type == "changed":
                    path = item["old"]["path"]
                else:
                    path = item["path"]
                if path not in by_file:
                    by_file[path] = {"added": [], "removed": [], "changed": []}
                by_file[path][change_type].append(item)

        return by_file

    def _count_diagnostics(self, data: JsonData) -> int:
        """Count the total number of diagnostics in the data."""
        total_diagnostics = 0
        for output in data["outputs"]:
            total_diagnostics += len(output.get("diagnostics", []))
        return total_diagnostics

    def _format_diagnostic(self, diag: Diagnostic) -> str:
        """Format a diagnostic entry as a string for comparison."""
        return (
            f"[{diag['level']}] {diag['lint_name']} - "
            f"{diag['path']}:{diag['line']}:{diag['column']} - {diag['message']}"
        )

    def _exit_statuses(self, project_data: RunOutput) -> list[ExitStatus]:
        """Return the project's observed exit statuses."""
        return project_data["exit_statuses"]

    def _total_runs(self, project_data: RunOutput) -> int:
        """Return the number of runs represented by an output."""
        return sum(status["count"] for status in self._exit_statuses(project_data))

    def _format_exit_statuses(self, statuses: list[ExitStatus]) -> str:
        """Format observed exit statuses for Markdown and raw diff output."""
        total = sum(status["count"] for status in statuses)
        parts = []
        for status in statuses:
            label = (
                "timeout"
                if status["return_code"] is None
                else f"exit {status['return_code']}"
            )
            if len(statuses) > 1:
                label += f" ({status['count']}/{total})"
            parts.append(label)
        return ", ".join(parts)

    def _stable_panic_messages(self, project_data: RunOutput) -> list[str]:
        """Return panic identities present in every represented run."""
        statuses = self._exit_statuses(project_data)
        variants_by_status = []
        all_keys = set()
        for status in statuses:
            variants_by_key: dict[str, list[OutputVariant]] = {}
            for variant in status.get("panic_messages", []):
                [key] = index_panic_messages([variant["message"]])
                all_keys.add(key)
                variants_by_key.setdefault(key, []).append(variant)
            variants_by_status.append(variants_by_key)

        stable = []
        for key in all_keys:
            stable_multiplicity = min(
                sum(
                    variant["count"] == status["count"]
                    for variant in variants.get(key, [])
                )
                for status, variants in zip(statuses, variants_by_status, strict=True)
            )
            representatives = [
                variant["message"]
                for variants in variants_by_status
                for variant in variants.get(key, [])
            ]
            stable.extend(representatives[:stable_multiplicity])
        return sorted(stable)

    def _project_status(self, project_data: RunOutput) -> _ProjectStatus:
        """Return the project's aggregate exit status."""
        has_normal_exit = any(
            status["return_code"] in (0, 1)
            for status in self._exit_statuses(project_data)
        )
        has_abnormal_exit = any(
            status["return_code"] not in (0, 1)
            for status in self._exit_statuses(project_data)
        )
        if has_normal_exit and has_abnormal_exit:
            return _ProjectStatus.FLAKY
        if not has_abnormal_exit:
            return _ProjectStatus.SUCCESS

        return_codes = {
            status["return_code"] for status in self._exit_statuses(project_data)
        }
        if return_codes == {None}:
            return _ProjectStatus.TIMEOUT
        return _ProjectStatus.ABNORMAL_EXIT

    def _has_flaky_exit_evidence(self, project_data: RunOutput) -> bool:
        """Return whether exit outcomes or their evidence varied between runs."""
        statuses = self._exit_statuses(project_data)
        if len(statuses) > 1:
            return True
        for status in statuses:
            if any(
                variant["count"] != status["count"]
                for variant in status.get("panic_messages", [])
            ):
                return True

            stderr_counts: Counter[str] = Counter()
            for variant in status.get("stderr", []):
                stderr_counts[normalize_stderr(variant["message"])] += variant["count"]
            if any(count != status["count"] for count in stderr_counts.values()):
                return True

        return False

    def _compare_flaky_exit_statuses(
        self, old_project: RunOutput, new_project: RunOutput
    ) -> dict[str, Any] | None:
        """Return a flaky exit-status change, ignoring frequency-only noise."""
        old_statuses = self._exit_statuses(old_project)
        new_statuses = self._exit_statuses(new_project)
        if not (
            self._has_flaky_exit_evidence(old_project)
            or self._has_flaky_exit_evidence(new_project)
        ):
            return None

        old_codes = {status["return_code"] for status in old_statuses}
        new_codes = {status["return_code"] for status in new_statuses}
        old_panic_evidence = Counter(
            (status["return_code"], key)
            for status in old_statuses
            for variant in status.get("panic_messages", [])
            for key in index_panic_messages([variant["message"]])
        )
        new_panic_evidence = Counter(
            (status["return_code"], key)
            for status in new_statuses
            for variant in status.get("panic_messages", [])
            for key in index_panic_messages([variant["message"]])
        )
        old_stderr_evidence = {
            (status["return_code"], normalize_stderr(variant["message"]))
            for status in old_statuses
            for variant in status.get("stderr", [])
        }
        new_stderr_evidence = {
            (status["return_code"], normalize_stderr(variant["message"]))
            for status in new_statuses
            for variant in status.get("stderr", [])
        }
        # Frequencies from a finite sample of an already-flaky project are
        # themselves noisy. Only a change in the observed outcome set is
        # reliable enough to surface as a flaky exit-status change. Panic
        # identities and stderr variants are evidence, rather than frequencies,
        # so preserve changes to those even when the return-code set is stable.
        if (
            old_codes == new_codes
            and old_panic_evidence == new_panic_evidence
            and old_stderr_evidence == new_stderr_evidence
        ):
            return None

        return {
            "old": old_statuses,
            "new": new_statuses,
            "old_runs": self._total_runs(old_project),
            "new_runs": self._total_runs(new_project),
        }

    def _compute_diffs(self) -> dict[str, Any]:
        """Compute differences between the old and new diagnostic data."""
        result = {
            "added_projects": [],
            "removed_projects": [],
            "modified_projects": [],
            "failed_projects": [],
            "flaky_exit_status_changes": [],
        }

        # Get project names from both files
        old_projects = {proj["project"]: proj for proj in self.old_data["outputs"]}
        new_projects = {proj["project"]: proj for proj in self.new_data["outputs"]}

        # Check for failed projects in common projects first
        common_projects = set(old_projects.keys()) & set(new_projects.keys())
        for project_name in sorted(common_projects):
            old_project = old_projects[project_name]
            new_project = new_projects[project_name]

            old_status = self._project_status(old_project)
            new_status = self._project_status(new_project)
            old_failed = old_status in {
                _ProjectStatus.TIMEOUT,
                _ProjectStatus.ABNORMAL_EXIT,
            }
            new_failed = new_status in {
                _ProjectStatus.TIMEOUT,
                _ProjectStatus.ABNORMAL_EXIT,
            }

            flaky_exit_status_change = self._compare_flaky_exit_statuses(
                old_project, new_project
            )
            if flaky_exit_status_change:
                result["flaky_exit_status_changes"].append({
                    "project": project_name,
                    "project_location": new_project.get("project_location", ""),
                    **flaky_exit_status_change,
                })

            old_panics = self._stable_panic_messages(old_project)
            new_panics = self._stable_panic_messages(new_project)

            failure_transition_is_flaky = _ProjectStatus.FLAKY in {
                old_status,
                new_status,
            }
            if not failure_transition_is_flaky and (old_failed or new_failed):
                introduced_panics, fixed_panics, persistent_panics = (
                    _compare_panic_messages(old_panics, new_panics)
                )
                abnormal_exit_kind_changed = (
                    old_status is _ProjectStatus.ABNORMAL_EXIT
                    and new_status is _ProjectStatus.ABNORMAL_EXIT
                    and {
                        status["return_code"]
                        for status in self._exit_statuses(old_project)
                    }
                    != {
                        status["return_code"]
                        for status in self._exit_statuses(new_project)
                    }
                )
                failure_mode_is_flaky = bool(
                    len(self._exit_statuses(old_project)) > 1
                    or len(self._exit_statuses(new_project)) > 1
                )

                # `failure_status` celebrates or flags overall state
                # transitions. A project that stays failing but picks up a new
                # panic is a regression, even if it also changes failure mode,
                # but it isn't a newly failing project. Otherwise, switching
                # between a timeout and an abnormal exit, or between different
                # abnormal return codes, is neutral: neither mode is
                # necessarily worse. A project that stays failing but loses a
                # panic is still failing, but the reduction is worth surfacing
                # as a partial improvement.
                if not old_failed and new_failed:
                    failure_status = "new"
                elif old_failed and not new_failed:
                    failure_status = "fixed"
                elif introduced_panics:
                    failure_status = "new_panics"
                elif not failure_mode_is_flaky and (
                    old_status != new_status or abnormal_exit_kind_changed
                ):
                    failure_status = "changed"
                elif fixed_panics and flaky_exit_status_change is None:
                    failure_status = "reduced"
                else:
                    failure_status = "persistent"

                entry = {
                    "project": project_name,
                    "project_location": new_project.get("project_location", ""),
                    "old_status": old_status.value,
                    "new_status": new_status.value,
                    "old_exit_statuses": self._exit_statuses(old_project),
                    "new_exit_statuses": self._exit_statuses(new_project),
                    "old_runs": self._total_runs(old_project),
                    "new_runs": self._total_runs(new_project),
                    "old_panic_messages": old_panics,
                    "new_panic_messages": new_panics,
                    "introduced_panic_messages": introduced_panics,
                    "fixed_panic_messages": fixed_panics,
                    "persistent_panic_messages": persistent_panics,
                    "failure_status": failure_status,
                }
                self._add_project_kind(entry, new_project)
                result["failed_projects"].append(entry)
                # Skip detailed diff analysis for failed projects
                continue

        # Find removed projects
        for project_name in sorted(old_projects.keys()):
            if project_name not in new_projects:
                project_data = old_projects[project_name]
                diagnostics = project_data.get("diagnostics", [])
                diagnostics = sorted(
                    diagnostics,
                    key=lambda d: (
                        d.get("path", ""),
                        d.get("line", 0),
                        d.get("column", 0),
                        d.get("message", ""),
                    ),
                )
                entry: dict[str, Any] = {
                    "project": project_name,
                    "project_location": project_data.get("project_location", ""),
                    "diagnostics": diagnostics,
                }
                self._add_project_kind(entry, project_data)
                flaky = project_data.get("flaky_diagnostics", [])
                if flaky:
                    entry["flaky_diagnostics"] = flaky
                    entry["flaky_runs"] = project_data.get("flaky_runs")
                entry["exit_statuses"] = self._exit_statuses(project_data)
                entry["exit_status_runs"] = self._total_runs(project_data)
                if len(self._exit_statuses(project_data)) > 1:
                    result["flaky_exit_status_changes"].append({
                        "project": project_name,
                        "project_location": project_data.get("project_location", ""),
                        "old": self._exit_statuses(project_data),
                        "new": [],
                        "old_runs": self._total_runs(project_data),
                        "new_runs": 0,
                    })
                result["removed_projects"].append(entry)

        # Find added projects
        for project_name in sorted(new_projects.keys()):
            if project_name not in old_projects:
                project_data = new_projects[project_name]
                diagnostics = project_data.get("diagnostics", [])
                diagnostics = sorted(
                    diagnostics,
                    key=lambda d: (
                        d.get("path", ""),
                        d.get("line", 0),
                        d.get("column", 0),
                        d.get("message", ""),
                    ),
                )
                entry: dict[str, Any] = {
                    "project": project_name,
                    "project_location": project_data.get("project_location", ""),
                    "diagnostics": diagnostics,
                }
                self._add_project_kind(entry, project_data)
                flaky = project_data.get("flaky_diagnostics", [])
                if flaky:
                    entry["flaky_diagnostics"] = flaky
                    entry["flaky_runs"] = project_data.get("flaky_runs")
                entry["exit_statuses"] = self._exit_statuses(project_data)
                entry["exit_status_runs"] = self._total_runs(project_data)
                if len(self._exit_statuses(project_data)) > 1:
                    result["flaky_exit_status_changes"].append({
                        "project": project_name,
                        "project_location": project_data.get("project_location", ""),
                        "old": [],
                        "new": self._exit_statuses(project_data),
                        "old_runs": 0,
                        "new_runs": self._total_runs(project_data),
                    })
                result["added_projects"].append(entry)

        # Get list of failed projects to exclude from detailed analysis
        failed_project_names = {proj["project"] for proj in result["failed_projects"]}

        # Find modified projects (excluding failed ones)
        for project_name in sorted(set(old_projects.keys()) & set(new_projects.keys())):
            if project_name in failed_project_names:
                continue  # Skip failed projects

            old_project = old_projects[project_name]
            new_project = new_projects[project_name]

            old_flaky = old_project.get("flaky_diagnostics", [])
            new_flaky = new_project.get("flaky_diagnostics", [])

            # Reconcile stable vs flaky: exclude stable diagnostics at
            # locations that are flaky on either side.  A stable diagnostic
            # at a flaky location is unreliable — it just happened to appear
            # in all N runs of this batch, but is nondeterministic.
            all_flaky_locs: set[tuple] = set()
            for loc in old_flaky:
                all_flaky_locs.add((loc["path"], loc["line"], loc["column"]))
            for loc in new_flaky:
                all_flaky_locs.add((loc["path"], loc["line"], loc["column"]))

            old_diagnostics = [
                d
                for d in old_project.get("diagnostics", [])
                if (d["path"], d["line"], d["column"]) not in all_flaky_locs
            ]
            new_diagnostics = [
                d
                for d in new_project.get("diagnostics", [])
                if (d["path"], d["line"], d["column"]) not in all_flaky_locs
            ]

            # Compare stable diagnostics
            old_diagnostics_by_file = self._group_diagnostics_by_file(old_diagnostics)
            new_diagnostics_by_file = self._group_diagnostics_by_file(new_diagnostics)

            file_diffs = self._compare_files(
                old_diagnostics_by_file, new_diagnostics_by_file
            )

            # Reconcile flaky vs stable: exclude flaky locations that also
            # exist (as stable or flaky) on the other side.
            old_all_locs = self._all_diagnostic_locations(old_project)
            new_all_locs = self._all_diagnostic_locations(new_project)
            old_flaky_filtered = self._exclude_known_overlaps(old_flaky, new_all_locs)
            new_flaky_filtered = self._exclude_known_overlaps(new_flaky, old_all_locs)

            # Compare flaky locations as grouped units
            flaky_diffs = self._compare_flaky_locations(
                old_flaky_filtered,
                new_flaky_filtered,
                old_project.get("flaky_runs"),
                new_project.get("flaky_runs"),
            )

            has_stable_changes = (
                file_diffs["added_files"]
                or file_diffs["removed_files"]
                or file_diffs["modified_files"]
            )
            has_flaky_changes = (
                flaky_diffs["added"] or flaky_diffs["removed"] or flaky_diffs["changed"]
            )

            if has_stable_changes or has_flaky_changes:
                entry: dict[str, Any] = {
                    "project": project_name,
                    "project_location": new_project.get("project_location", ""),
                    "diffs": file_diffs,
                }
                self._add_project_kind(entry, new_project)
                if has_flaky_changes:
                    entry["flaky_diffs"] = flaky_diffs
                    entry["flaky_file_diffs"] = self._organize_flaky_diffs_by_file(
                        flaky_diffs
                    )
                result["modified_projects"].append(entry)

        # Sort failed projects so PR reviewers see new regressions first,
        # then changed failure modes, reductions, persistent failures, and fixes.
        failure_status_priority = {
            "new": 6,
            "new_panics": 5,
            "changed": 4,
            "reduced": 3,
            "persistent": 2,
            "fixed": 1,
        }

        def failed_project_sort_key(project):
            status_priority = failure_status_priority[project["failure_status"]]
            old_abnormal = project["old_status"] == "abnormal exit"
            new_abnormal = project["new_status"] == "abnormal exit"
            old_timeout = project["old_status"] == "timeout"
            new_timeout = project["new_status"] == "timeout"

            if old_abnormal or new_abnormal:
                exit_priority = 2  # Abnormal exits (incl. panics/stack overflows) first
            elif old_timeout or new_timeout:
                exit_priority = 1  # Timeouts second
            else:
                exit_priority = 0

            # Negate so the natural sort puts the highest-priority entries
            # at the top without relying on `reverse=True` (which would also
            # reverse the project-name tiebreaker).
            return (-status_priority, -exit_priority, project["project"])

        result["failed_projects"].sort(key=failed_project_sort_key)

        return result

    @staticmethod
    def _project_kind(output: RunOutput) -> str | None:
        metadata = output.get("project_metadata")
        return metadata.get("kind") if metadata is not None else None

    @classmethod
    def _add_project_kind(cls, entry: dict[str, Any], output: RunOutput) -> None:
        if kind := cls._project_kind(output):
            entry["project_metadata"] = {"kind": kind}

    def _group_diagnostics_by_file(
        self, diagnostics: list[Diagnostic]
    ) -> dict[str, list[Diagnostic]]:
        """Group diagnostics by file path."""
        result = {}
        for diag in diagnostics:
            path = diag["path"]
            if path not in result:
                result[path] = []
            result[path].append(diag)
        return result

    def _compare_files(
        self,
        old_files: dict[str, list[Diagnostic]],
        new_files: dict[str, list[Diagnostic]],
    ) -> dict[str, Any]:
        """Compare diagnostics across files."""
        result = {"added_files": [], "removed_files": [], "modified_files": []}

        # Find removed files
        for file_path in sorted(old_files.keys()):
            if file_path not in new_files:
                diagnostics = old_files[file_path]
                # Sort diagnostics by line, column, message
                diagnostics = sorted(
                    diagnostics,
                    key=lambda d: (
                        d.get("line", 0),
                        d.get("column", 0),
                        d.get("message", ""),
                    ),
                )
                result["removed_files"].append({
                    "path": file_path,
                    "diagnostics": diagnostics,
                })

        # Find added files
        for file_path in sorted(new_files.keys()):
            if file_path not in old_files:
                diagnostics = new_files[file_path]
                # Sort diagnostics by line, column, message
                diagnostics = sorted(
                    diagnostics,
                    key=lambda d: (
                        d.get("line", 0),
                        d.get("column", 0),
                        d.get("message", ""),
                    ),
                )
                result["added_files"].append({
                    "path": file_path,
                    "diagnostics": diagnostics,
                })

        # Find modified files
        for file_path in sorted(set(old_files.keys()) & set(new_files.keys())):
            old_diagnostics = old_files[file_path]
            new_diagnostics = new_files[file_path]

            # Group diagnostics by line
            old_diagnostics_by_line = self._group_diagnostics_by_line(old_diagnostics)
            new_diagnostics_by_line = self._group_diagnostics_by_line(new_diagnostics)

            line_diffs = self._compare_lines(
                old_diagnostics_by_line, new_diagnostics_by_line
            )

            if (
                line_diffs["added_lines"]
                or line_diffs["removed_lines"]
                or line_diffs["modified_lines"]
            ):
                result["modified_files"].append({
                    "path": file_path,
                    "diffs": line_diffs,
                })

        return result

    def _group_diagnostics_by_line(
        self, diagnostics: list[Diagnostic]
    ) -> dict[int, list[Diagnostic]]:
        """Group diagnostics by line number."""
        result = {}
        for diag in diagnostics:
            line = diag["line"]
            if line not in result:
                result[line] = []
            result[line].append(diag)
        # Sort diagnostics within each line by column, message
        for line_num, diags in result.items():
            result[line_num] = sorted(
                diags,
                key=lambda d: (d.get("column", 0), d.get("message", "")),
            )
        return result

    def _compare_lines(
        self,
        old_lines: dict[int, list[Diagnostic]],
        new_lines: dict[int, list[Diagnostic]],
    ) -> dict[str, Any]:
        """Compare diagnostics across lines."""
        result = {"added_lines": [], "removed_lines": [], "modified_lines": []}

        # Find removed lines
        for line_num in sorted(old_lines.keys()):
            if line_num not in new_lines:
                diagnostics = old_lines[line_num]
                # Sort diagnostics by column, message
                diagnostics = sorted(
                    diagnostics,
                    key=lambda d: (d.get("column", 0), d.get("message", "")),
                )
                result["removed_lines"].append({
                    "line": line_num,
                    "diagnostics": diagnostics,
                })

        # Find added lines
        for line_num in sorted(new_lines.keys()):
            if line_num not in old_lines:
                diagnostics = new_lines[line_num]
                # Sort diagnostics by column, message
                diagnostics = sorted(
                    diagnostics,
                    key=lambda d: (d.get("column", 0), d.get("message", "")),
                )
                result["added_lines"].append({
                    "line": line_num,
                    "diagnostics": diagnostics,
                })

        # Find modified lines
        for line_num in sorted(set(old_lines.keys()) & set(new_lines.keys())):
            old_diagnostics = old_lines[line_num]
            new_diagnostics = new_lines[line_num]

            # Convert to formatted strings for comparison
            old_formatted = {self._format_diagnostic(d) for d in old_diagnostics}
            new_formatted = {self._format_diagnostic(d) for d in new_diagnostics}

            # Find differences
            removed = old_formatted - new_formatted
            added = new_formatted - old_formatted

            if removed or added:
                # Find line-by-line diffs for each diagnostic
                text_diffs = []
                changed_old_formatted = set()
                changed_new_formatted = set()

                # Track which new diagnostics have been matched to avoid double-matching
                matched_new_strs = set()

                # For simplicity, we'll just show all removed and added diagnostics
                for old_diag in old_diagnostics:
                    old_str = self._format_diagnostic(old_diag)
                    if old_str in removed:
                        for new_diag in new_diagnostics:
                            new_str = self._format_diagnostic(new_diag)
                            if (
                                new_str in added
                                and new_str not in matched_new_strs
                                and self._similar_diagnostics(old_diag, new_diag)
                            ):
                                # Generate line diff
                                diff = self._generate_text_diff(old_str, new_str)
                                if diff:
                                    text_diffs.append({
                                        "old": old_diag,
                                        "new": new_diag,
                                        "diff": diff,
                                    })
                                    changed_old_formatted.add(old_str)
                                    changed_new_formatted.add(new_str)
                                    matched_new_strs.add(new_str)
                                break

                # Filter out diagnostics that are part of changes
                removed_diagnostics = [
                    d
                    for d in old_diagnostics
                    if self._format_diagnostic(d) in removed
                    and self._format_diagnostic(d) not in changed_old_formatted
                ]
                added_diagnostics = [
                    d
                    for d in new_diagnostics
                    if self._format_diagnostic(d) in added
                    and self._format_diagnostic(d) not in changed_new_formatted
                ]
                # Sort removed and added diagnostics
                removed_diagnostics = sorted(
                    removed_diagnostics,
                    key=lambda d: (d.get("column", 0), d.get("message", "")),
                )
                added_diagnostics = sorted(
                    added_diagnostics,
                    key=lambda d: (d.get("column", 0), d.get("message", "")),
                )

                result["modified_lines"].append({
                    "line": line_num,
                    "removed": removed_diagnostics,
                    "added": added_diagnostics,
                    "text_diffs": text_diffs,
                })

        return result

    def _similar_diagnostics(self, diag1: Diagnostic, diag2: Diagnostic) -> bool:
        """Check if two diagnostics are similar (same lint name)."""
        return diag1["lint_name"] == diag2["lint_name"]

    def _generate_text_diff(self, old_text: str, new_text: str) -> list[str]:
        """Generate a text diff between two strings."""
        diff = difflib.ndiff(old_text.splitlines(), new_text.splitlines())
        return list(diff)

    def _calculate_statistics(self) -> DiffStatistics:
        """Calculate statistics about added, removed, and changed diagnostics.

        Flaky diagnostic diffs are excluded from the totals and breakdowns.
        Stable diagnostics from projects that happen to also have flaky data
        are still counted.
        """
        # Intermediate dictionaries (local variables)
        added_by_lint: dict[str, int] = {}
        removed_by_lint: dict[str, int] = {}
        changed_by_lint: dict[str, int] = {}
        added_by_project: dict[str, int] = {}
        removed_by_project: dict[str, int] = {}
        changed_by_project: dict[str, int] = {}

        total_added = 0
        total_removed = 0
        total_changed = 0

        # Count diagnostics from added projects
        for project in self.diffs["added_projects"]:
            project_name = project["project"]
            for diag in project["diagnostics"]:
                total_added += 1
                lint_name = diag.get("lint_name", "unknown")
                added_by_lint[lint_name] = added_by_lint.get(lint_name, 0) + 1
                added_by_project[project_name] = (
                    added_by_project.get(project_name, 0) + 1
                )

        # Count diagnostics from removed projects
        for project in self.diffs["removed_projects"]:
            project_name = project["project"]
            for diag in project["diagnostics"]:
                total_removed += 1
                lint_name = diag.get("lint_name", "unknown")
                removed_by_lint[lint_name] = removed_by_lint.get(lint_name, 0) + 1
                removed_by_project[project_name] = (
                    removed_by_project.get(project_name, 0) + 1
                )

        # Count diagnostics from modified projects
        for project in self.diffs["modified_projects"]:
            project_name = project["project"]
            # Added files in modified projects
            for file_data in project["diffs"].get("added_files", []):
                for diag in file_data["diagnostics"]:
                    total_added += 1
                    lint_name = diag.get("lint_name", "unknown")
                    added_by_lint[lint_name] = added_by_lint.get(lint_name, 0) + 1
                    added_by_project[project_name] = (
                        added_by_project.get(project_name, 0) + 1
                    )

            # Removed files in modified projects
            for file_data in project["diffs"].get("removed_files", []):
                for diag in file_data["diagnostics"]:
                    total_removed += 1
                    lint_name = diag.get("lint_name", "unknown")
                    removed_by_lint[lint_name] = removed_by_lint.get(lint_name, 0) + 1
                    removed_by_project[project_name] = (
                        removed_by_project.get(project_name, 0) + 1
                    )

            # Modified files in modified projects
            for file_data in project["diffs"].get("modified_files", []):
                # Added lines
                for line_data in file_data["diffs"].get("added_lines", []):
                    for diag in line_data["diagnostics"]:
                        total_added += 1
                        lint_name = diag.get("lint_name", "unknown")
                        added_by_lint[lint_name] = added_by_lint.get(lint_name, 0) + 1
                        added_by_project[project_name] = (
                            added_by_project.get(project_name, 0) + 1
                        )

                # Removed lines
                for line_data in file_data["diffs"].get("removed_lines", []):
                    for diag in line_data["diagnostics"]:
                        total_removed += 1
                        lint_name = diag.get("lint_name", "unknown")
                        removed_by_lint[lint_name] = (
                            removed_by_lint.get(lint_name, 0) + 1
                        )
                        removed_by_project[project_name] = (
                            removed_by_project.get(project_name, 0) + 1
                        )

                # Modified lines
                for line_data in file_data["diffs"].get("modified_lines", []):
                    # Count text_diffs as changed diagnostics
                    for diff_item in line_data.get("text_diffs", []):
                        total_changed += 1
                        lint_name = diff_item["old"].get("lint_name", "unknown")
                        changed_by_lint[lint_name] = (
                            changed_by_lint.get(lint_name, 0) + 1
                        )
                        changed_by_project[project_name] = (
                            changed_by_project.get(project_name, 0) + 1
                        )

                    # Count pure additions and removals (already filtered in diff computation)
                    for diag in line_data["added"]:
                        total_added += 1
                        lint_name = diag.get("lint_name", "unknown")
                        added_by_lint[lint_name] = added_by_lint.get(lint_name, 0) + 1
                        added_by_project[project_name] = (
                            added_by_project.get(project_name, 0) + 1
                        )

                    for diag in line_data["removed"]:
                        total_removed += 1
                        lint_name = diag.get("lint_name", "unknown")
                        removed_by_lint[lint_name] = (
                            removed_by_lint.get(lint_name, 0) + 1
                        )
                        removed_by_project[project_name] = (
                            removed_by_project.get(project_name, 0) + 1
                        )

            # Flaky location diffs are excluded from statistics — they
            # are still shown in the HTML report for manual inspection.

        # Create merged lint breakdown sorted by total absolute change (descending)
        all_lints = (
            set(added_by_lint.keys())
            | set(removed_by_lint.keys())
            | set(changed_by_lint.keys())
        )
        merged_lints: list[MergedLintStats] = []

        for lint_name in all_lints:
            added_count = added_by_lint.get(lint_name, 0)
            removed_count = removed_by_lint.get(lint_name, 0)
            changed_count = changed_by_lint.get(lint_name, 0)
            total_change = added_count + removed_count + changed_count
            merged_lints.append({
                "lint_name": lint_name,
                "added": added_count,
                "removed": removed_count,
                "changed": changed_count,
                "net_change": added_count - removed_count,
                "total_change": total_change,
            })

        # Sort by total absolute change (|removed| + |added| + |changed|) descending, then by name for ties
        merged_lints.sort(key=lambda x: (-x["total_change"], x["lint_name"]))

        # Identify projects with flaky diagnostics in the new data
        flaky_project_names: set[str] = {
            proj["project"]
            for proj in self.diffs["added_projects"]
            if proj.get("flaky_diagnostics")
        }

        # Create merged project breakdown sorted by total absolute change (descending)
        all_projects = (
            added_by_project.keys()
            | removed_by_project.keys()
            | changed_by_project.keys()
        )
        merged_projects: list[MergedProjectStats] = []

        for project_name in all_projects:
            added_count = added_by_project.get(project_name, 0)
            removed_count = removed_by_project.get(project_name, 0)
            changed_count = changed_by_project.get(project_name, 0)
            total_change = added_count + removed_count + changed_count
            merged_projects.append({
                "project_name": project_name,
                "added": added_count,
                "removed": removed_count,
                "changed": changed_count,
                "net_change": added_count - removed_count,
                "total_change": total_change,
                "is_flaky": project_name in flaky_project_names,
            })

        # Sort by total absolute change (|removed| + |added| + |changed|) descending, then by name for ties
        merged_projects.sort(key=lambda x: (-x["total_change"], x["project_name"]))

        return {
            "total_added": total_added,
            "total_removed": total_removed,
            "total_changed": total_changed,
            "failed_projects": len(self.diffs.get("failed_projects", [])),
            "merged_by_lint": merged_lints,
            "merged_by_project": merged_projects,
        }

    def _format_short_diagnostic(self, diag: Diagnostic) -> str:
        return (
            f"{diag['path']}:{diag['line']}:{diag['column']} "
            f"{diag['level']}[{diag['lint_name']}] {diag['message']}"
        )

    def introduced_project_failures(self) -> list[str]:
        """Return project names that regressed from a normal exit to an abnormal exit or timeout."""
        return [
            project["project"]
            for project in self.diffs.get("failed_projects", [])
            if project["failure_status"] == "new"
        ]

    def has_new_panics(self) -> bool:
        """Check if any still-failing project gained panic messages."""
        return any(
            project["failure_status"] == "new_panics"
            for project in self.diffs.get("failed_projects", [])
        )

    def generate_comment_title(self) -> str:
        """Generate the PR comment title with status indicators for new failures."""
        title = "## `ecosystem-analyzer` results"
        failed_projects = self.diffs.get("failed_projects", [])
        failure_statuses = {project["failure_status"] for project in failed_projects}
        new_projects = [
            project for project in failed_projects if project["failure_status"] == "new"
        ]
        if new_projects:
            # If every regression is a timeout, say so — a blanket "new
            # crashes detected" would be misleading.  Any panic or crash
            # in the mix falls back to "crashes" so the title stays short.
            if all(
                _failure_descriptor(project, "new") == "timeout"
                for project in new_projects
            ):
                title += ": new timeouts detected ❌"
            else:
                title += ": new crashes detected ❌"
        elif "new_panics" in failure_statuses:
            title += ": new panics detected ❌"
        elif "fixed" in failure_statuses:
            title += ": ecosystem failure fixed 🎉"
        elif "reduced" in failure_statuses:
            title += ": panics reduced 🎉"
        return title

    def _render_raw_diff_sections(
        self, sections: dict[str, list[tuple[list[str], bool]]]
    ) -> list[str]:
        lines: list[str] = []
        for header in sorted(sections):
            lines.append(header)
            for entry_lines, _counts_as_change in sections[header]:
                lines.extend(entry_lines)
            lines.append("")

        if lines:
            lines.pop()

        return lines

    def _has_flaky_changes(self) -> bool:
        """Check whether any flaky changes were omitted from the raw diff."""
        if self.diffs.get("flaky_exit_status_changes"):
            return True
        if any(
            project.get("flaky_diagnostics")
            for project in chain(
                self.diffs["removed_projects"], self.diffs["added_projects"]
            )
        ):
            return True
        for project in self.diffs["modified_projects"]:
            flaky_diffs = project.get("flaky_diffs", {})
            if (
                flaky_diffs.get("added")
                or flaky_diffs.get("removed")
                or flaky_diffs.get("changed")
            ):
                return True
        return False

    def _raw_diff_sections(
        self,
    ) -> tuple[dict[str, list[tuple[list[str], bool]]], int]:
        sections: dict[str, list[tuple[list[str], bool]]] = {}

        def add_entry(
            project_name: str,
            project_location: str | None,
            lines: list[str],
            *,
            counts_as_change: bool = True,
        ) -> None:
            if project_location:
                header = f"{project_name} ({project_location})"
            else:
                header = project_name
            sections.setdefault(header, []).append((lines, counts_as_change))

        for project in self.diffs["failed_projects"]:
            status = project["failure_status"]
            introduced_panics = project["introduced_panic_messages"]
            fixed_panics = project["fixed_panic_messages"]
            # Persistent failures aren't PR-specific news; they live in the
            # HTML report instead of the comment's raw diff. Changed failure
            # modes are neutral but still PR-specific. Reduced failures (some
            # panics resolved, project still failing) are surfaced here so
            # reviewers can see the partial progress.
            if status == "persistent":
                continue
            # Prefix drives GitHub's `diff` highlighting: `-` is red (bad),
            # `+` is green (good). Newly failing and new panics are red; fully
            # fixed and partial fixes on still-failing projects are green.
            match status:
                case "new":
                    line = (
                        f"- FAILED "
                        f"old={project['old_status']}"
                        f"({self._format_exit_statuses(project['old_exit_statuses'])}) "
                        f"new={project['new_status']}"
                        f"({self._format_exit_statuses(project['new_exit_statuses'])})"
                    )
                case "new_panics":
                    line = (
                        f"- NEW PANIC{'S' if len(introduced_panics) != 1 else ''}: "
                        f"{len(introduced_panics)} introduced, project still failing"
                    )
                case "fixed":
                    line = (
                        f"+ FIXED "
                        f"old={project['old_status']}"
                        f"({self._format_exit_statuses(project['old_exit_statuses'])}) "
                        f"new={project['new_status']}"
                        f"({self._format_exit_statuses(project['new_exit_statuses'])})"
                    )
                case "changed":
                    line = (
                        f"  FAILURE MODE CHANGED "
                        f"old={project['old_status']}"
                        f"({self._format_exit_statuses(project['old_exit_statuses'])}) "
                        f"new={project['new_status']}"
                        f"({self._format_exit_statuses(project['new_exit_statuses'])})"
                    )
                case _:
                    line = (
                        f"+ PARTIAL FIX {len(fixed_panics)} "
                        f"panic{'s' if len(fixed_panics) != 1 else ''} "
                        f"resolved, project still failing"
                    )
            add_entry(
                project["project"],
                project.get("project_location"),
                [line],
                counts_as_change=False,
            )

        for project in self.diffs["removed_projects"]:
            project_name = project["project"]
            project_location = project.get("project_location")
            for diag in project["diagnostics"]:
                add_entry(
                    project_name,
                    project_location,
                    [f"- {self._format_short_diagnostic(diag)}"],
                )
        for project in self.diffs["added_projects"]:
            project_name = project["project"]
            project_location = project.get("project_location")
            for diag in project["diagnostics"]:
                add_entry(
                    project_name,
                    project_location,
                    [f"+ {self._format_short_diagnostic(diag)}"],
                )
        for project in self.diffs["modified_projects"]:
            project_name = project["project"]
            project_location = project.get("project_location")
            diffs = project["diffs"]

            for file_data in diffs.get("removed_files", []):
                for diag in file_data["diagnostics"]:
                    add_entry(
                        project_name,
                        project_location,
                        [f"- {self._format_short_diagnostic(diag)}"],
                    )

            for file_data in diffs.get("added_files", []):
                for diag in file_data["diagnostics"]:
                    add_entry(
                        project_name,
                        project_location,
                        [f"+ {self._format_short_diagnostic(diag)}"],
                    )

            for file_data in diffs.get("modified_files", []):
                for line_data in file_data["diffs"].get("removed_lines", []):
                    for diag in line_data["diagnostics"]:
                        add_entry(
                            project_name,
                            project_location,
                            [f"- {self._format_short_diagnostic(diag)}"],
                        )

                for line_data in file_data["diffs"].get("added_lines", []):
                    for diag in line_data["diagnostics"]:
                        add_entry(
                            project_name,
                            project_location,
                            [f"+ {self._format_short_diagnostic(diag)}"],
                        )

                for line_data in file_data["diffs"].get("modified_lines", []):
                    for diff_item in line_data.get("text_diffs", []):
                        add_entry(
                            project_name,
                            project_location,
                            [
                                f"- {self._format_short_diagnostic(diff_item['old'])}",
                                f"+ {self._format_short_diagnostic(diff_item['new'])}",
                            ],
                        )
                    for diag in line_data["removed"]:
                        add_entry(
                            project_name,
                            project_location,
                            [f"- {self._format_short_diagnostic(diag)}"],
                        )
                    for diag in line_data["added"]:
                        add_entry(
                            project_name,
                            project_location,
                            [f"+ {self._format_short_diagnostic(diag)}"],
                        )

        total_changes = sum(
            1
            for entries in sections.values()
            for _lines, counts_as_change in entries
            if counts_as_change
        )
        return sections, total_changes

    def render_statistics_markdown(
        self,
        *,
        inline_threshold: int = 15,
    ) -> str:
        statistics = self._calculate_statistics()
        failed_projects = self.diffs.get("failed_projects", [])

        markdown_content = self.generate_comment_title() + "\n\n"

        # Projects with the same failure mode on the baseline and PR aren't
        # news — omit them from the PR-comment summary table so reviewers can
        # focus on what changed. They still show up in the full HTML report.
        table_projects = [
            project
            for project in failed_projects
            if project["failure_status"] != "persistent"
        ]

        if table_projects:
            markdown_content += "**Failing projects**:\n\n"
            markdown_content += (
                "| Project | Status | Old Status | New Status | "
                "Old Outcomes | New Outcomes |\n"
            )
            markdown_content += (
                "|---------|--------|------------|------------|"
                "-----------------|------------------|\n"
            )

            for project in table_projects:
                old_status = project["old_status"]
                new_status = project["new_status"]
                old_outcomes = self._format_exit_statuses(project["old_exit_statuses"])
                new_outcomes = self._format_exit_statuses(project["new_exit_statuses"])
                status_label = _FAILURE_STATUS_LABELS[project["failure_status"]]

                markdown_content += (
                    f"| `{project['project']}` | {status_label} | "
                    f"{old_status} | {new_status} | "
                    f"`{old_outcomes}` | `{new_outcomes}` |\n"
                )

            markdown_content += "\n"

        if (
            statistics["total_added"] == 0
            and statistics["total_removed"] == 0
            and statistics["total_changed"] == 0
        ):
            markdown_content += "No diagnostic changes detected ✅\n"
        else:
            if table_projects:
                markdown_content += "**Diagnostic changes:**\n"

            markdown_content += """
| Lint rule | Added | Removed | Changed |
|-----------|------:|--------:|--------:|
"""

            for lint_data in statistics["merged_by_lint"]:
                markdown_content += (
                    f"| `{lint_data['lint_name']}` | {lint_data['added']:,} | "
                    f"{lint_data['removed']:,} | {lint_data['changed']:,} |\n"
                )

            markdown_content += (
                f"| **Total** | **{statistics['total_added']:,}** | "
                f"**{statistics['total_removed']:,}** | "
                f"**{statistics['total_changed']:,}** |\n"
            )

        large_timing_changes = self._large_timing_changes()
        if large_timing_changes:
            markdown_content += (
                "\n\n**Large timing changes**:\n\n"
                "| Project | Old Time | New Time | Change |\n"
                "|---------|---------:|---------:|-------:|\n"
            )

            for row in large_timing_changes:
                markdown_content += (
                    f"| `{row['project']}` | {row['old_time']:.2f}s | "
                    f"{row['new_time']:.2f}s | {row['change_percent']:+.0f}% |\n"
                )

        raw_diff_sections, total_raw_diff_changes = self._raw_diff_sections()
        raw_diff_lines = self._render_raw_diff_sections(raw_diff_sections)

        if self._has_flaky_changes():
            markdown_content += (
                "\n\n_Flaky changes detected. "
                "This PR summary excludes flaky changes; see the HTML report for details._"
            )

        if not raw_diff_lines:
            return markdown_content

        markdown_content += "\n\n"

        # Determine the character budget available for the raw diff block.
        # We account for the wrapping markup (details/summary tags, code
        # fence, sampling note) so that the final comment stays within
        # GitHub's character limit.
        char_budget = (
            self.GITHUB_COMMENT_CHAR_LIMIT
            - self.GITHUB_COMMENT_CHAR_MARGIN
            - len(markdown_content)
        )

        # Reserve space for the static wrapper markup that surrounds the
        # diff content.  We estimate generously so the budget refers to
        # the diff payload itself.
        #   - code fence: "```diff\n" + "\n```" = 12 chars
        #   - details/summary (worst case): ~80 chars
        #   - sampling note (worst case): ~120 chars
        #   - extra newlines / padding: ~30 chars
        _wrapper_overhead = 250
        char_budget -= _wrapper_overhead

        displayed_lines = raw_diff_lines
        sampled = False
        displayed_change_count = total_raw_diff_changes

        full_diff_text = "\n".join(raw_diff_lines)
        needs_sampling = len(full_diff_text) > char_budget

        if needs_sampling:
            sampled = True
            rng = random.Random(self.RAW_DIFF_SAMPLE_SEED)

            # Build a list of (header, index, char_cost) for every
            # change entry so we can greedily pick as many as fit.
            change_entries: list[tuple[str, int, int]] = []
            for header, entries in sorted(raw_diff_sections.items()):
                for index, (lines, counts_as_change) in enumerate(entries):
                    if counts_as_change:
                        # +1 for the newline joining
                        cost = sum(len(line) + 1 for line in lines)
                        change_entries.append((header, index, cost))

            # Shuffle deterministically, then greedily pick entries that
            # fit within the character budget.
            rng.shuffle(change_entries)

            # Account for non-change lines (headers, etc.) that will
            # always be included.  Compute their cost first.
            non_change_cost = 0
            for header, entries in sorted(raw_diff_sections.items()):
                # header line + newline
                non_change_cost += len(header) + 1
                for _lines, counts_as_change in entries:
                    if not counts_as_change:
                        non_change_cost += sum(len(line) + 1 for line in _lines)
                # trailing blank line between sections
                non_change_cost += 1

            remaining = char_budget - non_change_cost
            selected_entries: set[tuple[str, int]] = set()
            for entry_header, entry_index, cost in change_entries:
                if cost <= remaining:
                    selected_entries.add((entry_header, entry_index))
                    remaining -= cost

            displayed_change_count = len(selected_entries)

            displayed_sections: dict[str, list[tuple[list[str], bool]]] = {}
            for header, entries in sorted(raw_diff_sections.items()):
                kept_entries = []
                for index, entry in enumerate(entries):
                    entry_lines, counts_as_change = entry
                    if not counts_as_change or (header, index) in selected_entries:
                        kept_entries.append((entry_lines, counts_as_change))
                if kept_entries:
                    displayed_sections[header] = kept_entries

            displayed_lines = self._render_raw_diff_sections(displayed_sections)

        if sampled:
            markdown_content += (
                f"_Showing a random sample of "
                f"{displayed_change_count} of {total_raw_diff_changes} changes. "
                "See the HTML report for the full diff._\n\n"
            )

        raw_diff_block = "```diff\n" + "\n".join(displayed_lines) + "\n```"

        if total_raw_diff_changes < inline_threshold:
            markdown_content += "**Raw diff:**\n\n"
            markdown_content += raw_diff_block
        else:
            summary = "Raw diff"
            if sampled:
                summary += f" sample ({displayed_change_count} of {total_raw_diff_changes} changes)"
            else:
                summary += f" ({total_raw_diff_changes} changes)"
            markdown_content += f"<details>\n<summary>{summary}</summary>\n\n"
            markdown_content += raw_diff_block
            markdown_content += "\n</details>"

        return markdown_content

    def generate_html_report(self, output_path: str) -> None:
        """Generate an HTML report of the diagnostic differences."""
        # Set up Jinja2 environment with package loader
        try:
            # Try PackageLoader first (works for installed packages)
            env = Environment(loader=PackageLoader("ecosystem_analyzer", "templates"))
        except (ImportError, FileNotFoundError):
            # Fallback to FileSystemLoader for development
            template_path = Path(__file__).parent.parent.parent / "templates"
            if not template_path.exists():
                template_path = Path("templates")
            env = Environment(loader=FileSystemLoader(str(template_path)))

        template = env.get_template("diff.html")

        # Calculate statistics
        statistics = self._calculate_statistics()

        # Create template context
        context = {
            "old_commit": self.old_commit,
            "new_commit": self.new_commit,
            "old_branch_info": self.old_branch_info,
            "new_branch_info": self.new_branch_info,
            "ty_repo_url": self.ty_repo_url,
            "old_diagnostics": self.old_diagnostics,
            "new_diagnostics": self.new_diagnostics,
            "diffs": self.diffs,
            "statistics": statistics,
            "failure_descriptor": _failure_descriptor,
            "failure_status_labels": _FAILURE_STATUS_LABELS,
            "failure_status_titles": _FAILURE_STATUS_TITLES,
            "project_kinds": sorted({
                kind
                for output in chain(self.old_data["outputs"], self.new_data["outputs"])
                if (kind := self._project_kind(output))
            }),
        }

        # Ensure the output directory exists
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        # Render the template and write to file
        with open(output_path, "w") as f:
            f.write(template.render(context))

        print(f"HTML report generated at: {output_path}")

    def save_json_diff(self, output_path: str) -> None:
        """Save the computed diffs as a JSON file."""
        with open(output_path, "w") as f:
            json.dump(self.diffs, f, indent=2)

        print(f"JSON diff saved to: {output_path}")

    def generate_timing_html_report(self, output_path: str) -> None:
        """Generate an HTML report comparing timing data between old and new runs."""
        # Get timing data for comparison
        timing_data = self._compute_timing_comparison()

        # Set up Jinja2 environment with package loader
        try:
            # Try PackageLoader first (works for installed packages)
            env = Environment(loader=PackageLoader("ecosystem_analyzer", "templates"))
        except (ImportError, FileNotFoundError):
            # Fallback to FileSystemLoader for development
            template_path = Path(__file__).parent.parent.parent / "templates"
            if not template_path.exists():
                template_path = Path("templates")
            env = Environment(loader=FileSystemLoader(str(template_path)))

        template = env.get_template("timing_diff.html")

        # Calculate summary statistics
        summary = self._calculate_timing_summary(timing_data)

        # Create template context
        context = {
            "old_commit": self.old_commit,
            "new_commit": self.new_commit,
            "old_branch_info": self.old_branch_info,
            "new_branch_info": self.new_branch_info,
            "timing_data": timing_data,
            "summary": summary,
        }

        # Ensure the output directory exists
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        # Render the template and write to file
        with open(output_path, "w") as f:
            f.write(template.render(context))

        print(f"Timing diff HTML report generated at: {output_path}")

    def _compute_timing_comparison(self) -> list[dict[str, Any]]:
        """Compute timing comparison data between old and new runs."""
        # Get project timing data from both files
        old_projects = {proj["project"]: proj for proj in self.old_data["outputs"]}
        new_projects = {proj["project"]: proj for proj in self.new_data["outputs"]}

        timing_data = []

        # Find projects that exist in both old and new data
        common_projects = set(old_projects.keys()) & set(new_projects.keys())

        for project_name in sorted(common_projects):
            old_project = old_projects[project_name]
            new_project = new_projects[project_name]

            # A timing comparison cannot represent a project that only
            # succeeds on some runs without turning a flaky exit into a
            # misleading improvement or regression.
            old_status = self._project_status(old_project)
            new_status = self._project_status(new_project)
            if _ProjectStatus.FLAKY in {
                old_status,
                new_status,
            }:
                continue

            old_time = old_project.get("median_time_s")
            new_time = new_project.get("median_time_s")

            # Imported diagnostic output may have a known successful exit status
            # without a measured runtime. It has no useful timing comparison.
            if (
                old_status is _ProjectStatus.SUCCESS
                and new_status is _ProjectStatus.SUCCESS
                and (old_time is None or new_time is None)
            ):
                continue

            old_is_timeout = old_status is _ProjectStatus.TIMEOUT
            new_is_timeout = new_status is _ProjectStatus.TIMEOUT
            old_is_abnormal = old_status is _ProjectStatus.ABNORMAL_EXIT
            new_is_abnormal = new_status is _ProjectStatus.ABNORMAL_EXIT

            # Handle different failure cases
            if (old_is_timeout or old_is_abnormal) and (
                new_is_timeout or new_is_abnormal
            ):
                # Both failed (timeout or abnormal)
                factor = 1.0
                is_failed = True
                failure_type = "both_failed"
            elif old_is_timeout or old_is_abnormal:
                # Old failed, new succeeded
                factor = 0.0  # Special case for template
                is_failed = True
                failure_type = "old_failed"
            elif new_is_timeout or new_is_abnormal:
                # New failed, old succeeded
                factor = float("inf")  # Special case for template
                is_failed = True
                failure_type = "new_failed"
            else:
                assert old_time is not None
                assert new_time is not None

                # Neither failed, calculate normal factor
                if old_time > 0:
                    factor = new_time / old_time
                else:
                    factor = float("inf") if new_time > 0 else 1.0
                is_failed = False
                failure_type = None

            timing_data.append({
                "project": project_name,
                "old_time": old_time,
                "new_time": new_time,
                "factor": factor,
                "is_failed": is_failed,
                "failure_type": failure_type,
                "old_is_timeout": old_is_timeout,
                "new_is_timeout": new_is_timeout,
                "old_is_abnormal": old_is_abnormal,
                "new_is_abnormal": new_is_abnormal,
            })

        # Sort by failure type first (abnormal exits, then timeouts, then normal), then by factor significance
        def sort_key(x):
            if x["old_is_abnormal"] or x["new_is_abnormal"]:
                return (2, 0)  # Abnormal exits first
            if x["old_is_timeout"] or x["new_is_timeout"]:
                return (1, 0)  # Timeouts second
            return (
                0,
                abs(x["factor"] - 1.0),
            )  # Normal projects by factor significance

        timing_data.sort(key=sort_key, reverse=True)

        return timing_data

    def _calculate_timing_summary(
        self, timing_data: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Calculate summary statistics for timing comparison."""
        if not timing_data:
            return {
                "speedups": 0,
                "slowdowns": 0,
                "timeouts": 0,
                "abnormal_exits": 0,
                "avg_factor": 1.0,
            }

        # Filter out failed runs and infinite values for statistical calculations
        valid_data = [row for row in timing_data if not row.get("is_failed", False)]
        factors = [row["factor"] for row in valid_data if row["factor"] != float("inf")]

        speedups = sum(1 for row in valid_data if row["factor"] < 0.9)
        slowdowns = sum(1 for row in valid_data if row["factor"] > 1.1)
        timeouts = sum(
            1
            for row in timing_data
            if row.get("old_is_timeout", False) or row.get("new_is_timeout", False)
        )
        abnormal_exits = sum(
            1
            for row in timing_data
            if row.get("old_is_abnormal", False) or row.get("new_is_abnormal", False)
        )

        avg_factor = sum(factors) / len(factors) if factors else 1.0

        return {
            "speedups": speedups,
            "slowdowns": slowdowns,
            "timeouts": timeouts,
            "abnormal_exits": abnormal_exits,
            "avg_factor": avg_factor,
        }

    def _large_timing_changes(self) -> list[dict[str, Any]]:
        """Return projects whose runtime changed substantially between runs."""
        threshold = self.LARGE_TIMING_CHANGE_THRESHOLD
        timing_data = self._compute_timing_comparison()
        large_changes = []

        for row in timing_data:
            if row.get("is_failed", False):
                continue

            factor = row["factor"]
            if abs(factor - 1.0) < threshold:
                continue

            old_time = row["old_time"]
            new_time = row["new_time"]
            if old_time is None or new_time is None:
                continue

            large_changes.append({
                "project": row["project"],
                "old_time": old_time,
                "new_time": new_time,
                "factor": factor,
                "change_percent": (factor - 1.0) * 100,
            })

        large_changes.sort(key=lambda row: abs(row["factor"] - 1.0), reverse=True)
        return large_changes

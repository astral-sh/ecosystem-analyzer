import difflib
import json
import os
from pathlib import Path
from typing import Any, TypedDict

from jinja2 import Environment, FileSystemLoader, PackageLoader

from .diagnostic import Diagnostic
from .run_output import RunOutput


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


class DiffStatistics(TypedDict):
    """Statistics about diagnostic changes."""

    total_added: int
    total_removed: int
    total_changed: int
    failed_projects: int
    merged_by_lint: list[MergedLintStats]
    merged_by_project: list[MergedProjectStats]


class DiagnosticDiff:
    """Class for comparing diagnostic data between two JSON files."""

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

        # Filter out diagnostics with specific message:
        message_filter = "No overload of bound method `__init__` matches arguments"
        for output in data["outputs"]:
            if "diagnostics" in output:
                output["diagnostics"] = [
                    diag
                    for diag in output["diagnostics"]
                    if message_filter not in diag.get("message", "")
                ]

        return data

    def _get_commit(self, data: JsonData) -> str:
        ty_commits = set(
            output.get("ty_commit", "unknown")
            for output in data["outputs"]
            if output.get("ty_commit") is not None
        )

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

    def _exclude_flaky_overlaps(
        self, diagnostics: list[Diagnostic], other_flaky: list
    ) -> list[Diagnostic]:
        """Remove stable diagnostics at locations that are flaky on the other side.

        A diagnostic at a location that's nondeterministic on the other side
        should not appear in the stable diff — it's the same underlying
        flaky behavior, just with different classification luck.
        """
        if not other_flaky:
            return diagnostics

        flaky_locs: set[tuple] = set()
        for loc in other_flaky:
            flaky_locs.add((loc["path"], loc["line"], loc["column"]))

        return [
            d for d in diagnostics
            if (d["path"], d["line"], d["column"]) not in flaky_locs
        ]

    def _all_diagnostic_locations(self, project: dict) -> set[tuple]:
        """Build a set of (path, line, column) locations from all diagnostics.

        Includes locations from both stable diagnostics and flaky variants.
        """
        locs: set[tuple] = set()
        for d in project.get("diagnostics", []):
            locs.add((d["path"], d["line"], d["column"]))
        for loc in project.get("flaky_diagnostics", []):
            locs.add((loc["path"], loc["line"], loc["column"]))
        return locs

    def _all_diagnostic_keys(self, project: dict) -> set[tuple]:
        """Build a set of (path, line, column, message) keys from all diagnostics.

        Includes both stable diagnostics and all flaky variants.
        """
        keys: set[tuple] = set()
        for d in project.get("diagnostics", []):
            keys.add((d["path"], d["line"], d["column"], d["message"]))
        for loc in project.get("flaky_diagnostics", []):
            for v in loc["variants"]:
                d = v["diagnostic"]
                keys.add((d["path"], d["line"], d["column"], d["message"]))
        return keys

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
            loc for loc in flaky_locations
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

        old_by_loc = {(loc["path"], loc["line"], loc["column"]): loc for loc in old_flaky}
        new_by_loc = {(loc["path"], loc["line"], loc["column"]): loc for loc in new_flaky}

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
            if self._flaky_location_variant_set(old_loc) != self._flaky_location_variant_set(new_loc):
                old_annotated = dict(old_loc)
                old_annotated["flaky_runs"] = old_flaky_runs
                new_annotated = dict(new_loc)
                new_annotated["flaky_runs"] = new_flaky_runs
                result["changed"].append({"old": old_annotated, "new": new_annotated})

        return result

    def _organize_flaky_diffs_by_file(self, flaky_diffs: dict[str, list]) -> dict[str, dict[str, list]]:
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

    def _is_project_failed(self, project_data: RunOutput) -> tuple[bool, str]:
        """Check if a project failed (timeout or abnormal exit) and return status."""
        return_code = project_data.get("return_code")
        time_s = project_data.get("time_s")

        if return_code is None:
            return True, "timeout"
        elif return_code not in (0, 1):
            return True, "abnormal exit"
        elif time_s is None:
            return True, "abnormal exit"
        else:
            return False, "success"

    def _compute_diffs(self) -> dict[str, Any]:
        """Compute differences between the old and new diagnostic data."""
        result = {
            "added_projects": [],
            "removed_projects": [],
            "modified_projects": [],
            "failed_projects": [],
        }

        # Get project names from both files
        old_projects = {proj["project"]: proj for proj in self.old_data["outputs"]}
        new_projects = {proj["project"]: proj for proj in self.new_data["outputs"]}

        # Check for failed projects in common projects first
        common_projects = set(old_projects.keys()) & set(new_projects.keys())
        for project_name in sorted(common_projects):
            old_project = old_projects[project_name]
            new_project = new_projects[project_name]

            old_failed, old_status = self._is_project_failed(old_project)
            new_failed, new_status = self._is_project_failed(new_project)

            if old_failed or new_failed:
                result["failed_projects"].append(
                    {
                        "project": project_name,
                        "project_location": new_project.get("project_location", ""),
                        "old_status": old_status,
                        "new_status": new_status,
                        "old_return_code": old_project.get("return_code"),
                        "new_return_code": new_project.get("return_code"),
                    }
                )
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
                flaky = project_data.get("flaky_diagnostics", [])
                if flaky:
                    entry["flaky_diagnostics"] = flaky
                    entry["flaky_runs"] = project_data.get("flaky_runs")
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
                flaky = project_data.get("flaky_diagnostics", [])
                if flaky:
                    entry["flaky_diagnostics"] = flaky
                    entry["flaky_runs"] = project_data.get("flaky_runs")
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
                d for d in old_project.get("diagnostics", [])
                if (d["path"], d["line"], d["column"]) not in all_flaky_locs
            ]
            new_diagnostics = [
                d for d in new_project.get("diagnostics", [])
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
                old_flaky_filtered, new_flaky_filtered,
                old_project.get("flaky_runs"),
                new_project.get("flaky_runs"),
            )

            has_stable_changes = (
                file_diffs["added_files"]
                or file_diffs["removed_files"]
                or file_diffs["modified_files"]
            )
            has_flaky_changes = (
                flaky_diffs["added"]
                or flaky_diffs["removed"]
                or flaky_diffs["changed"]
            )

            if has_stable_changes or has_flaky_changes:
                entry: dict[str, Any] = {
                    "project": project_name,
                    "project_location": new_project.get("project_location", ""),
                    "diffs": file_diffs,
                }
                if has_flaky_changes:
                    entry["flaky_diffs"] = flaky_diffs
                    entry["flaky_file_diffs"] = self._organize_flaky_diffs_by_file(flaky_diffs)
                result["modified_projects"].append(entry)

        # Sort failed projects to prioritize abnormal exits over timeouts
        def failed_project_sort_key(project):
            old_abnormal = project["old_status"] == "abnormal exit"
            new_abnormal = project["new_status"] == "abnormal exit"
            old_timeout = project["old_status"] == "timeout"
            new_timeout = project["new_status"] == "timeout"

            if old_abnormal or new_abnormal:
                return (2, project["project"])  # Abnormal exits first
            elif old_timeout or new_timeout:
                return (1, project["project"])  # Timeouts second
            else:
                return (0, project["project"])  # Other failures last

        result["failed_projects"].sort(key=failed_project_sort_key, reverse=True)

        return result

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
                result["removed_files"].append(
                    {"path": file_path, "diagnostics": diagnostics}
                )

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
                result["added_files"].append(
                    {"path": file_path, "diagnostics": diagnostics}
                )

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
                result["modified_files"].append(
                    {"path": file_path, "diffs": line_diffs}
                )

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
        for line_num in result:
            result[line_num] = sorted(
                result[line_num],
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
                result["removed_lines"].append(
                    {"line": line_num, "diagnostics": diagnostics}
                )

        # Find added lines
        for line_num in sorted(new_lines.keys()):
            if line_num not in old_lines:
                diagnostics = new_lines[line_num]
                # Sort diagnostics by column, message
                diagnostics = sorted(
                    diagnostics,
                    key=lambda d: (d.get("column", 0), d.get("message", "")),
                )
                result["added_lines"].append(
                    {"line": line_num, "diagnostics": diagnostics}
                )

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
                                    text_diffs.append(
                                        {"old": old_diag, "new": new_diag, "diff": diff}
                                    )
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

                result["modified_lines"].append(
                    {
                        "line": line_num,
                        "removed": removed_diagnostics,
                        "added": added_diagnostics,
                        "text_diffs": text_diffs,
                    }
                )

        return result

    def _similar_diagnostics(self, diag1: Diagnostic, diag2: Diagnostic) -> bool:
        """Check if two diagnostics are similar (same lint name)."""
        return diag1["lint_name"] == diag2["lint_name"]

    def _generate_text_diff(self, old_text: str, new_text: str) -> list[str]:
        """Generate a text diff between two strings."""
        diff = difflib.ndiff(old_text.splitlines(), new_text.splitlines())
        return list(diff)

    def _calculate_statistics(self) -> DiffStatistics:
        """Calculate statistics about added, removed, and changed diagnostics."""
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
                added_by_project[project_name] = added_by_project.get(project_name, 0) + 1

        # Count diagnostics from removed projects
        for project in self.diffs["removed_projects"]:
            project_name = project["project"]
            for diag in project["diagnostics"]:
                total_removed += 1
                lint_name = diag.get("lint_name", "unknown")
                removed_by_lint[lint_name] = removed_by_lint.get(lint_name, 0) + 1
                removed_by_project[project_name] = removed_by_project.get(project_name, 0) + 1

        # Count diagnostics from modified projects
        for project in self.diffs["modified_projects"]:
            project_name = project["project"]
            # Added files in modified projects
            for file_data in project["diffs"].get("added_files", []):
                for diag in file_data["diagnostics"]:
                    total_added += 1
                    lint_name = diag.get("lint_name", "unknown")
                    added_by_lint[lint_name] = added_by_lint.get(lint_name, 0) + 1
                    added_by_project[project_name] = added_by_project.get(project_name, 0) + 1

            # Removed files in modified projects
            for file_data in project["diffs"].get("removed_files", []):
                for diag in file_data["diagnostics"]:
                    total_removed += 1
                    lint_name = diag.get("lint_name", "unknown")
                    removed_by_lint[lint_name] = removed_by_lint.get(lint_name, 0) + 1
                    removed_by_project[project_name] = removed_by_project.get(project_name, 0) + 1

            # Modified files in modified projects
            for file_data in project["diffs"].get("modified_files", []):
                # Added lines
                for line_data in file_data["diffs"].get("added_lines", []):
                    for diag in line_data["diagnostics"]:
                        total_added += 1
                        lint_name = diag.get("lint_name", "unknown")
                        added_by_lint[lint_name] = added_by_lint.get(lint_name, 0) + 1
                        added_by_project[project_name] = added_by_project.get(project_name, 0) + 1

                # Removed lines
                for line_data in file_data["diffs"].get("removed_lines", []):
                    for diag in line_data["diagnostics"]:
                        total_removed += 1
                        lint_name = diag.get("lint_name", "unknown")
                        removed_by_lint[lint_name] = removed_by_lint.get(lint_name, 0) + 1
                        removed_by_project[project_name] = removed_by_project.get(project_name, 0) + 1

                # Modified lines
                for line_data in file_data["diffs"].get("modified_lines", []):
                    # Count text_diffs as changed diagnostics
                    for diff_item in line_data.get("text_diffs", []):
                        total_changed += 1
                        lint_name = diff_item["old"].get("lint_name", "unknown")
                        changed_by_lint[lint_name] = changed_by_lint.get(lint_name, 0) + 1
                        changed_by_project[project_name] = changed_by_project.get(project_name, 0) + 1

                    # Count pure additions and removals (already filtered in diff computation)
                    for diag in line_data["added"]:
                        total_added += 1
                        lint_name = diag.get("lint_name", "unknown")
                        added_by_lint[lint_name] = added_by_lint.get(lint_name, 0) + 1
                        added_by_project[project_name] = added_by_project.get(project_name, 0) + 1

                    for diag in line_data["removed"]:
                        total_removed += 1
                        lint_name = diag.get("lint_name", "unknown")
                        removed_by_lint[lint_name] = removed_by_lint.get(lint_name, 0) + 1
                        removed_by_project[project_name] = removed_by_project.get(project_name, 0) + 1

            # Count flaky location diffs (each location = 1 diagnostic)
            flaky_diffs = project.get("flaky_diffs", {})
            for loc in flaky_diffs.get("added", []):
                total_added += 1
                # Use the first variant's lint_name as representative
                lint_name = loc["variants"][0]["diagnostic"]["lint_name"] if loc["variants"] else "unknown"
                added_by_lint[lint_name] = added_by_lint.get(lint_name, 0) + 1
                added_by_project[project_name] = added_by_project.get(project_name, 0) + 1

            for loc in flaky_diffs.get("removed", []):
                total_removed += 1
                lint_name = loc["variants"][0]["diagnostic"]["lint_name"] if loc["variants"] else "unknown"
                removed_by_lint[lint_name] = removed_by_lint.get(lint_name, 0) + 1
                removed_by_project[project_name] = removed_by_project.get(project_name, 0) + 1

            for change in flaky_diffs.get("changed", []):
                total_changed += 1
                lint_name = change["old"]["variants"][0]["diagnostic"]["lint_name"] if change["old"]["variants"] else "unknown"
                changed_by_lint[lint_name] = changed_by_lint.get(lint_name, 0) + 1
                changed_by_project[project_name] = changed_by_project.get(project_name, 0) + 1

        # Create merged lint breakdown sorted by total absolute change (descending)
        all_lints = (
            set(added_by_lint.keys())
            | set(removed_by_lint.keys())
            | set(changed_by_lint.keys())
        )
        merged_lints = []

        for lint_name in all_lints:
            added_count = added_by_lint.get(lint_name, 0)
            removed_count = removed_by_lint.get(lint_name, 0)
            changed_count = changed_by_lint.get(lint_name, 0)
            total_change = added_count + removed_count + changed_count
            merged_lints.append(
                {
                    "lint_name": lint_name,
                    "added": added_count,
                    "removed": removed_count,
                    "changed": changed_count,
                    "net_change": added_count - removed_count,
                    "total_change": total_change,
                }
            )

        # Sort by total absolute change (|removed| + |added| + |changed|) descending, then by name for ties
        merged_lints.sort(key=lambda x: (-x["total_change"], x["lint_name"]))

        # Create merged project breakdown sorted by total absolute change (descending)
        all_projects = (
            set(added_by_project.keys())
            | set(removed_by_project.keys())
            | set(changed_by_project.keys())
        )
        merged_projects = []

        for project_name in all_projects:
            added_count = added_by_project.get(project_name, 0)
            removed_count = removed_by_project.get(project_name, 0)
            changed_count = changed_by_project.get(project_name, 0)
            total_change = added_count + removed_count + changed_count
            merged_projects.append(
                {
                    "project_name": project_name,
                    "added": added_count,
                    "removed": removed_count,
                    "changed": changed_count,
                    "net_change": added_count - removed_count,
                    "total_change": total_change,
                }
            )

        # Sort by total absolute change (|removed| + |added| + |changed|) descending, then by name for ties
        merged_projects.sort(key=lambda x: (-x["total_change"], x["project_name"]))

        # Identify projects with flaky diagnostics in the new data
        flaky_project_names: set[str] = set()
        for output in self.new_data["outputs"]:
            if output.get("flaky_diagnostics"):
                flaky_project_names.add(output["project"])

        # Add flaky label to project entries, and include flaky-only projects
        for project_data in merged_projects:
            project_data["is_flaky"] = project_data["project_name"] in flaky_project_names

        # Add projects that are flaky but have no diffstat changes
        projects_in_merged = {p["project_name"] for p in merged_projects}
        for project_name in sorted(flaky_project_names - projects_in_merged):
            merged_projects.append({
                "project_name": project_name,
                "added": 0,
                "removed": 0,
                "changed": 0,
                "net_change": 0,
                "total_change": 0,
                "is_flaky": True,
            })

        # Re-sort: flaky-only projects (0 total change) go last
        merged_projects.sort(key=lambda x: (-x["total_change"], x["project_name"]))

        return {
            "total_added": total_added,
            "total_removed": total_removed,
            "total_changed": total_changed,
            "failed_projects": len(self.diffs.get("failed_projects", [])),
            "merged_by_lint": merged_lints,
            "merged_by_project": merged_projects,
        }

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

            # Get timing data and return codes
            old_time = old_project.get("time_s")
            new_time = new_project.get("time_s")
            old_return_code = old_project.get("return_code")
            new_return_code = new_project.get("return_code")

            # Determine the status based on return codes and timing data
            old_is_timeout = old_time is None
            new_is_timeout = new_time is None
            old_is_abnormal = old_return_code is not None and old_return_code not in (
                0,
                1,
            )
            new_is_abnormal = new_return_code is not None and new_return_code not in (
                0,
                1,
            )

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

            timing_data.append(
                {
                    "project": project_name,
                    "old_time": old_time,
                    "new_time": new_time,
                    "old_return_code": old_return_code,
                    "new_return_code": new_return_code,
                    "factor": factor,
                    "is_failed": is_failed,
                    "failure_type": failure_type,
                    "old_is_timeout": old_is_timeout,
                    "new_is_timeout": new_is_timeout,
                    "old_is_abnormal": old_is_abnormal,
                    "new_is_abnormal": new_is_abnormal,
                }
            )

        # Sort by failure type first (abnormal exits, then timeouts, then normal), then by factor significance
        def sort_key(x):
            if x["old_is_abnormal"] or x["new_is_abnormal"]:
                return (2, 0)  # Abnormal exits first
            elif x["old_is_timeout"] or x["new_is_timeout"]:
                return (1, 0)  # Timeouts second
            else:
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

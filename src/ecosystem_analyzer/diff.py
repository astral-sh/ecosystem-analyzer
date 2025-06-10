import difflib
import json
import os
import re
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader


class DiagnosticDiff:
    """Class for comparing diagnostic data between two JSON files."""

    def __init__(self, old_file: str, new_file: str):
        """Initialize with paths to the old and new JSON files."""
        self.old_file = old_file
        self.new_file = new_file
        self.ty_repo_url = "https://github.com/astral-sh/ruff"
        self.old_data = self._load_json(old_file)
        self.new_data = self._load_json(new_file)

        self.old_commit = self._get_commit(self.old_data)
        self.new_commit = self._get_commit(self.new_data)
        
        # Extract branch information from filenames
        self.old_branch_info = self._extract_branch_info(old_file)
        self.new_branch_info = self._extract_branch_info(new_file)

        self.old_diagnostics = self._count_diagnostics(self.old_data)
        self.new_diagnostics = self._count_diagnostics(self.new_data)

        self.diffs = self._compute_diffs()

    def _load_json(self, file_path: str) -> dict[str, Any]:
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

    def _get_commit(self, data) -> str:
        ty_commits = set(output["ty_commit"] for output in data["outputs"])
        if len(ty_commits) != 1:
            raise RuntimeError(
                "Error: The JSON file must contain diagnostics from a single ty commit."
            )
        return ty_commits.pop()

    def _extract_branch_info(self, file_path: str) -> str:
        """Extract branch/commit information from filename."""
        filename = Path(file_path).name
        
        # Pattern: diagnostics-{prefix}-{branch_or_commit}.json
        # Examples: diagnostics-old-main.json, diagnostics-new-attr-subscript-narrowing.json
        match = re.match(r"diagnostics-(?:old|new)-(.+)\.json$", filename)
        if match:
            return match.group(1)
        
        # Fallback: just use filename without extension
        return Path(file_path).stem

    def _count_diagnostics(self, data) -> int:
        """Count the total number of diagnostics in the data."""
        total_diagnostics = 0
        for output in data["outputs"]:
            total_diagnostics += len(output.get("diagnostics", []))
        return total_diagnostics

    def _format_diagnostic(self, diag: dict[str, Any]) -> str:
        """Format a diagnostic entry as a string for comparison."""
        return (
            f"[{diag['level']}] {diag['lint_name']} - "
            f"{diag['path']}:{diag['line']}:{diag['column']} - {diag['message']}"
        )

    def _compute_diffs(self) -> dict[str, Any]:
        """Compute differences between the old and new diagnostic data."""
        result = {"added_projects": [], "removed_projects": [], "modified_projects": []}

        # Get project names from both files
        old_projects = {
            proj["project"]: proj for proj in self.old_data.get("outputs", [])
        }
        new_projects = {
            proj["project"]: proj for proj in self.new_data.get("outputs", [])
        }

        # Find removed projects
        for project_name in sorted(old_projects.keys()):
            if project_name not in new_projects:
                project_data = old_projects[project_name]
                diagnostics = project_data.get("diagnostics", [])
                # Sort diagnostics by path, line, column, message
                diagnostics = sorted(diagnostics, key=lambda d: (d.get("path", ""), d.get("line", 0), d.get("column", 0), d.get("message", "")))
                result["removed_projects"].append(
                    {
                        "project": project_name,
                        "project_location": project_data.get("project_location", ""),
                        "diagnostics": diagnostics,
                    }
                )

        # Find added projects
        for project_name in sorted(new_projects.keys()):
            if project_name not in old_projects:
                project_data = new_projects[project_name]
                diagnostics = project_data.get("diagnostics", [])
                # Sort diagnostics by path, line, column, message
                diagnostics = sorted(diagnostics, key=lambda d: (d.get("path", ""), d.get("line", 0), d.get("column", 0), d.get("message", "")))
                result["added_projects"].append(
                    {
                        "project": project_name,
                        "project_location": project_data.get("project_location", ""),
                        "diagnostics": diagnostics,
                    }
                )

        # Find modified projects
        for project_name in sorted(set(old_projects.keys()) & set(new_projects.keys())):
            old_project = old_projects[project_name]
            new_project = new_projects[project_name]

            # Organize diagnostics by file path
            old_diagnostics_by_file = self._group_diagnostics_by_file(
                old_project.get("diagnostics", [])
            )
            new_diagnostics_by_file = self._group_diagnostics_by_file(
                new_project.get("diagnostics", [])
            )

            file_diffs = self._compare_files(
                old_diagnostics_by_file, new_diagnostics_by_file
            )

            if (
                file_diffs["added_files"]
                or file_diffs["removed_files"]
                or file_diffs["modified_files"]
            ):
                result["modified_projects"].append(
                    {
                        "project": project_name,
                        "project_location": new_project.get("project_location", ""),
                        "diffs": file_diffs,
                    }
                )

        return result

    def _group_diagnostics_by_file(
        self, diagnostics: list[dict[str, Any]]
    ) -> dict[str, list[dict[str, Any]]]:
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
        old_files: dict[str, list[dict[str, Any]]],
        new_files: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Compare diagnostics across files."""
        result = {"added_files": [], "removed_files": [], "modified_files": []}

        # Find removed files
        for file_path in sorted(old_files.keys()):
            if file_path not in new_files:
                diagnostics = old_files[file_path]
                # Sort diagnostics by line, column, message
                diagnostics = sorted(diagnostics, key=lambda d: (d.get("line", 0), d.get("column", 0), d.get("message", "")))
                result["removed_files"].append(
                    {"path": file_path, "diagnostics": diagnostics}
                )

        # Find added files
        for file_path in sorted(new_files.keys()):
            if file_path not in old_files:
                diagnostics = new_files[file_path]
                # Sort diagnostics by line, column, message
                diagnostics = sorted(diagnostics, key=lambda d: (d.get("line", 0), d.get("column", 0), d.get("message", "")))
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
        self, diagnostics: list[dict[str, Any]]
    ) -> dict[int, list[dict[str, Any]]]:
        """Group diagnostics by line number."""
        result = {}
        for diag in diagnostics:
            line = diag["line"]
            if line not in result:
                result[line] = []
            result[line].append(diag)
        # Sort diagnostics within each line by column, message
        for line_num in result:
            result[line_num] = sorted(result[line_num], key=lambda d: (d.get("column", 0), d.get("message", "")))
        return result

    def _compare_lines(
        self,
        old_lines: dict[int, list[dict[str, Any]]],
        new_lines: dict[int, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Compare diagnostics across lines."""
        result = {"added_lines": [], "removed_lines": [], "modified_lines": []}

        # Find removed lines
        for line_num in sorted(old_lines.keys()):
            if line_num not in new_lines:
                diagnostics = old_lines[line_num]
                # Sort diagnostics by column, message
                diagnostics = sorted(diagnostics, key=lambda d: (d.get("column", 0), d.get("message", "")))
                result["removed_lines"].append(
                    {"line": line_num, "diagnostics": diagnostics}
                )

        # Find added lines
        for line_num in sorted(new_lines.keys()):
            if line_num not in old_lines:
                diagnostics = new_lines[line_num]
                # Sort diagnostics by column, message
                diagnostics = sorted(diagnostics, key=lambda d: (d.get("column", 0), d.get("message", "")))
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

                # For simplicity, we'll just show all removed and added diagnostics
                for old_diag in old_diagnostics:
                    old_str = self._format_diagnostic(old_diag)
                    if old_str in removed:
                        for new_diag in new_diagnostics:
                            new_str = self._format_diagnostic(new_diag)
                            if new_str in added and self._similar_diagnostics(
                                old_diag, new_diag
                            ):
                                # Generate line diff
                                diff = self._generate_text_diff(old_str, new_str)
                                if diff:
                                    text_diffs.append(
                                        {"old": old_diag, "new": new_diag, "diff": diff}
                                    )
                                    changed_old_formatted.add(old_str)
                                    changed_new_formatted.add(new_str)
                                break

                # Filter out diagnostics that are part of changes
                removed_diagnostics = [
                    d
                    for d in old_diagnostics
                    if self._format_diagnostic(d) in removed and self._format_diagnostic(d) not in changed_old_formatted
                ]
                added_diagnostics = [
                    d
                    for d in new_diagnostics
                    if self._format_diagnostic(d) in added and self._format_diagnostic(d) not in changed_new_formatted
                ]
                # Sort removed and added diagnostics
                removed_diagnostics = sorted(removed_diagnostics, key=lambda d: (d.get("column", 0), d.get("message", "")))
                added_diagnostics = sorted(added_diagnostics, key=lambda d: (d.get("column", 0), d.get("message", "")))
                
                result["modified_lines"].append(
                    {
                        "line": line_num,
                        "removed": removed_diagnostics,
                        "added": added_diagnostics,
                        "text_diffs": text_diffs,
                    }
                )

        return result

    def _similar_diagnostics(
        self, diag1: dict[str, Any], diag2: dict[str, Any]
    ) -> bool:
        """Check if two diagnostics are similar (same lint name and position)."""
        return (
            diag1["lint_name"] == diag2["lint_name"]
            and diag1["column"] == diag2["column"]
        )

    def _generate_text_diff(self, old_text: str, new_text: str) -> list[str]:
        """Generate a text diff between two strings."""
        diff = difflib.ndiff(old_text.splitlines(), new_text.splitlines())
        return list(diff)

    def _calculate_statistics(self) -> dict[str, Any]:
        """Calculate statistics about added, removed, and changed diagnostics."""
        stats = {
            "total_added": 0,
            "total_removed": 0,
            "total_changed": 0,
            "added_by_lint": {},
            "removed_by_lint": {},
            "changed_by_lint": {}
        }

        # Count diagnostics from added projects
        for project in self.diffs["added_projects"]:
            for diag in project["diagnostics"]:
                stats["total_added"] += 1
                lint_name = diag.get("lint_name", "unknown")
                stats["added_by_lint"][lint_name] = stats["added_by_lint"].get(lint_name, 0) + 1

        # Count diagnostics from removed projects
        for project in self.diffs["removed_projects"]:
            for diag in project["diagnostics"]:
                stats["total_removed"] += 1
                lint_name = diag.get("lint_name", "unknown")
                stats["removed_by_lint"][lint_name] = stats["removed_by_lint"].get(lint_name, 0) + 1

        # Count diagnostics from modified projects
        for project in self.diffs["modified_projects"]:
            # Added files in modified projects
            for file_data in project["diffs"].get("added_files", []):
                for diag in file_data["diagnostics"]:
                    stats["total_added"] += 1
                    lint_name = diag.get("lint_name", "unknown")
                    stats["added_by_lint"][lint_name] = stats["added_by_lint"].get(lint_name, 0) + 1

            # Removed files in modified projects
            for file_data in project["diffs"].get("removed_files", []):
                for diag in file_data["diagnostics"]:
                    stats["total_removed"] += 1
                    lint_name = diag.get("lint_name", "unknown")
                    stats["removed_by_lint"][lint_name] = stats["removed_by_lint"].get(lint_name, 0) + 1

            # Modified files in modified projects
            for file_data in project["diffs"].get("modified_files", []):
                # Added lines
                for line_data in file_data["diffs"].get("added_lines", []):
                    for diag in line_data["diagnostics"]:
                        stats["total_added"] += 1
                        lint_name = diag.get("lint_name", "unknown")
                        stats["added_by_lint"][lint_name] = stats["added_by_lint"].get(lint_name, 0) + 1

                # Removed lines
                for line_data in file_data["diffs"].get("removed_lines", []):
                    for diag in line_data["diagnostics"]:
                        stats["total_removed"] += 1
                        lint_name = diag.get("lint_name", "unknown")
                        stats["removed_by_lint"][lint_name] = stats["removed_by_lint"].get(lint_name, 0) + 1

                # Modified lines
                for line_data in file_data["diffs"].get("modified_lines", []):
                    # Count text_diffs as changed diagnostics
                    for diff_item in line_data.get("text_diffs", []):
                        stats["total_changed"] += 1
                        lint_name = diff_item["old"].get("lint_name", "unknown")
                        stats["changed_by_lint"][lint_name] = stats["changed_by_lint"].get(lint_name, 0) + 1
                    
                    # Count pure additions and removals (already filtered in diff computation)
                    for diag in line_data["added"]:
                        stats["total_added"] += 1
                        lint_name = diag.get("lint_name", "unknown")
                        stats["added_by_lint"][lint_name] = stats["added_by_lint"].get(lint_name, 0) + 1
                    
                    for diag in line_data["removed"]:
                        stats["total_removed"] += 1
                        lint_name = diag.get("lint_name", "unknown")
                        stats["removed_by_lint"][lint_name] = stats["removed_by_lint"].get(lint_name, 0) + 1

        # Create merged lint breakdown sorted by total absolute change (descending)
        all_lints = set(stats["added_by_lint"].keys()) | set(stats["removed_by_lint"].keys()) | set(stats["changed_by_lint"].keys())
        merged_lints = []
        
        for lint_name in all_lints:
            added_count = stats["added_by_lint"].get(lint_name, 0)
            removed_count = stats["removed_by_lint"].get(lint_name, 0)
            changed_count = stats["changed_by_lint"].get(lint_name, 0)
            total_change = added_count + removed_count + changed_count
            merged_lints.append({
                "lint_name": lint_name,
                "added": added_count,
                "removed": removed_count,
                "changed": changed_count,
                "net_change": added_count - removed_count,
                "total_change": total_change
            })
        
        # Sort by total absolute change (|removed| + |added| + |changed|) descending, then by name for ties
        merged_lints.sort(key=lambda x: (-x["total_change"], x["lint_name"]))
        stats["merged_by_lint"] = merged_lints

        return stats

    def generate_html_report(self, output_path: str) -> None:
        """Generate an HTML report of the diagnostic differences."""
        # Set up Jinja2 environment
        env = Environment(loader=FileSystemLoader("templates"))
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

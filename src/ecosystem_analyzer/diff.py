import difflib
import json
import os
from typing import Any

from jinja2 import Environment, FileSystemLoader


class DiagnosticDiff:
    """Class for comparing diagnostic data between two JSON files."""

    def __init__(self, old_file: str, new_file: str):
        """Initialize with paths to the old and new JSON files."""
        self.old_file = old_file
        self.new_file = new_file
        self.old_data = self._load_json(old_file)
        self.new_data = self._load_json(new_file)

        self.old_commit = self._get_commit(self.old_data)
        self.new_commit = self._get_commit(self.new_data)

        self.old_diagnostics = self._count_diagnostics(self.old_data)
        self.new_diagnostics = self._count_diagnostics(self.new_data)

        self.diffs = self._compute_diffs()

    def _load_json(self, file_path: str) -> dict[str, Any]:
        """Load and parse a JSON file."""
        with open(file_path) as f:
            data = json.load(f)

        return data

    def _get_commit(self, data) -> str:
        red_knot_commits = set(output["red_knot_commit"] for output in data["outputs"])
        if len(red_knot_commits) != 1:
            raise RuntimeError(
                "Error: The JSON file must contain diagnostics from a single Red Knot commit."
            )
        return red_knot_commits.pop()

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
        for project_name, project_data in old_projects.items():
            if project_name not in new_projects:
                result["removed_projects"].append(
                    {
                        "project": project_name,
                        "project_location": project_data.get("project_location", ""),
                        "diagnostics": project_data.get("diagnostics", []),
                    }
                )

        # Find added projects
        for project_name, project_data in new_projects.items():
            if project_name not in old_projects:
                result["added_projects"].append(
                    {
                        "project": project_name,
                        "project_location": project_data.get("project_location", ""),
                        "diagnostics": project_data.get("diagnostics", []),
                    }
                )

        # Find modified projects
        for project_name in set(old_projects.keys()) & set(new_projects.keys()):
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
        for file_path, diagnostics in old_files.items():
            if file_path not in new_files:
                result["removed_files"].append(
                    {"path": file_path, "diagnostics": diagnostics}
                )

        # Find added files
        for file_path, diagnostics in new_files.items():
            if file_path not in old_files:
                result["added_files"].append(
                    {"path": file_path, "diagnostics": diagnostics}
                )

        # Find modified files
        for file_path in set(old_files.keys()) & set(new_files.keys()):
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
        return result

    def _compare_lines(
        self,
        old_lines: dict[int, list[dict[str, Any]]],
        new_lines: dict[int, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Compare diagnostics across lines."""
        result = {"added_lines": [], "removed_lines": [], "modified_lines": []}

        # Find removed lines
        for line_num, diagnostics in old_lines.items():
            if line_num not in new_lines:
                result["removed_lines"].append(
                    {"line": line_num, "diagnostics": diagnostics}
                )

        # Find added lines
        for line_num, diagnostics in new_lines.items():
            if line_num not in old_lines:
                result["added_lines"].append(
                    {"line": line_num, "diagnostics": diagnostics}
                )

        # Find modified lines
        for line_num in set(old_lines.keys()) & set(new_lines.keys()):
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
                                break

                result["modified_lines"].append(
                    {
                        "line": line_num,
                        "removed": [
                            d
                            for d in old_diagnostics
                            if self._format_diagnostic(d) in removed
                        ],
                        "added": [
                            d
                            for d in new_diagnostics
                            if self._format_diagnostic(d) in added
                        ],
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

    def generate_html_report(self, output_path: str) -> None:
        """Generate an HTML report of the diagnostic differences."""
        # Set up Jinja2 environment
        env = Environment(loader=FileSystemLoader("templates"))
        template = env.get_template("diff.html")

        # Create template context
        context = {
            "old_commit": self.old_commit,
            "new_commit": self.new_commit,
            "old_diagnostics": self.old_diagnostics,
            "new_diagnostics": self.new_diagnostics,
            "diffs": self.diffs,
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

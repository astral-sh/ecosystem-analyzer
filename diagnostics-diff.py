import json
import difflib
import argparse
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple


class DiagnosticDiff:
    """Class for comparing diagnostic data between two JSON files."""

    def __init__(self, old_file: str, new_file: str):
        """Initialize with paths to the old and new JSON files."""
        self.old_file = old_file
        self.new_file = new_file
        self.old_data = self._load_json(old_file)
        self.new_data = self._load_json(new_file)
        self.diffs = self._compute_diffs()

    def _load_json(self, file_path: str) -> Dict[str, Any]:
        """Load and parse a JSON file."""
        with open(file_path, "r") as f:
            return json.load(f)

    def _format_diagnostic(self, diag: Dict[str, Any]) -> str:
        """Format a diagnostic entry as a string for comparison."""
        return (
            f"[{diag['level']}] {diag['lint_name']} - "
            f"{diag['path']}:{diag['line']}:{diag['column']} - {diag['message']}"
        )

    def _compute_diffs(self) -> Dict[str, Any]:
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
        self, diagnostics: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
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
        old_files: Dict[str, List[Dict[str, Any]]],
        new_files: Dict[str, List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
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
        self, diagnostics: List[Dict[str, Any]]
    ) -> Dict[int, List[Dict[str, Any]]]:
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
        old_lines: Dict[int, List[Dict[str, Any]]],
        new_lines: Dict[int, List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
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
        self, diag1: Dict[str, Any], diag2: Dict[str, Any]
    ) -> bool:
        """Check if two diagnostics are similar (same lint name and position)."""
        return (
            diag1["lint_name"] == diag2["lint_name"]
            and diag1["column"] == diag2["column"]
        )

    def _generate_text_diff(self, old_text: str, new_text: str) -> List[str]:
        """Generate a text diff between two strings."""
        diff = difflib.ndiff(old_text.splitlines(), new_text.splitlines())
        return list(diff)

    def generate_html_report(self, output_path: str) -> None:
        """Generate an HTML report of the diagnostic differences."""
        html = self._generate_html()

        with open(output_path, "w") as f:
            f.write(html)

        print(f"HTML report generated at: {output_path}")

    def _generate_html(self) -> str:
        """Generate HTML content for the report."""
        html = """
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Diagnostic Diff Report</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    margin: 0;
                    padding: 20px;
                    color: #333;
                }
                .container {
                    max-width: 2000px;
                    margin: 0 auto;
                }
                h1, h2, h3, h4 {
                    margin-top: 20px;
                    margin-bottom: 10px;
                }
                .project {
                    margin-bottom: 30px;
                    border: 1px solid #ddd;
                    border-radius: 5px;
                    padding: 15px;
                }
                .project-header {
                    background-color: #f7f7f7;
                    padding: 10px;
                    margin: -15px -15px 15px -15px;
                    border-bottom: 1px solid #ddd;
                    border-radius: 5px 5px 0 0;
                }
                .file {
                    margin-bottom: 20px;
                    border: 1px solid #eee;
                    border-radius: 3px;
                    padding: 10px;
                }
                .file-header {
                    background-color: #f3f3f3;
                    padding: 5px 10px;
                    margin: -10px -10px 10px -10px;
                    border-bottom: 1px solid #eee;
                    border-radius: 3px 3px 0 0;
                    font-family: monospace;
                }
                .line {
                    margin-bottom: 15px;
                    padding: 5px;
                    background-color: #f9f9f9;
                    border-radius: 3px;
                }
                .diagnostic {
                    font-family: monospace;
                    margin: 5px 0;
                    padding: 3px;
                }
                .added {
                    background-color: #e6ffed;
                    border-left: 3px solid #2cbe4e;
                }
                .removed {
                    background-color: #ffeef0;
                    border-left: 3px solid #cb2431;
                }
                .modified {
                    background-color: #f9f9f9;
                    border-left: 3px solid #0366d6;
                }
                .diff-line {
                    font-family: monospace;
                    white-space: pre-wrap;
                    margin: 0;
                    padding: 2px 5px;
                }
                .diff-added {
                    background-color: #e6ffed;
                }
                .diff-removed {
                    background-color: #ffeef0;
                }
                .error {
                    color: #cb2431;
                    font-weight: bold;
                }
                .warning {
                    color: #b08800;
                    font-weight: bold;
                }
                .summary {
                    margin-bottom: 20px;
                    padding: 10px;
                    background-color: #f3f3f3;
                    border-radius: 5px;
                }
                .collapsible {
                    cursor: pointer;
                }
                .content {
                    display: block;
                    overflow: hidden;
                }
                .expand-all {
                    margin-bottom: 10px;
                    cursor: pointer;
                    padding: 5px 10px;
                    background-color: #f3f3f3;
                    border: none;
                    border-radius: 3px;
                }
            </style>
        </head>
        <body>
            <div class="container">"""

        html += """
                <h1>Diagnostic Diff Report</h1>
                <p>Comparing:</p>
                <ul>
                    <li><strong>Old:</strong> {}</li>
                    <li><strong>New:</strong> {}</li>
                </ul>

                <div class="summary">
                    <h2>Summary</h2>
                    <p><strong>Added Projects:</strong> {}</p>
                    <p><strong>Removed Projects:</strong> {}</p>
                    <p><strong>Modified Projects:</strong> {}</p>
                </div>

                <button class="expand-all" onclick="toggleAll()">Collapse All</button>
        """.format(
            self.old_file,
            self.new_file,
            len(self.diffs["added_projects"]),
            len(self.diffs["removed_projects"]),
            len(self.diffs["modified_projects"]),
        )

        # Added Projects
        if self.diffs["added_projects"]:
            html += "<h2>Added Projects</h2>"
            for project in self.diffs["added_projects"]:
                html += self._render_project(project, "added")

        # Removed Projects
        if self.diffs["removed_projects"]:
            html += "<h2>Removed Projects</h2>"
            for project in self.diffs["removed_projects"]:
                html += self._render_project(project, "removed")

        # Modified Projects
        if self.diffs["modified_projects"]:
            html += "<h2>Modified Projects</h2>"
            for project in self.diffs["modified_projects"]:
                html += self._render_modified_project(project)

        # Add JavaScript for collapsible sections
        html += """
                <script>
                    function toggleContent(elementId) {
                        var content = document.getElementById(elementId);
                        if (content.style.display === "block") {
                            content.style.display = "none";
                        } else {
                            content.style.display = "block";
                        }
                    }

                    function toggleAll() {
                        var contents = document.getElementsByClassName("content");
                        var expandAllButton = document.getElementsByClassName("expand-all")[0];
                        
                        // Check if at least one is hidden
                        var anyHidden = false;
                        for (var i = 0; i < contents.length; i++) {
                            if (contents[i].style.display !== "block") {
                                anyHidden = true;
                                break;
                            }
                        }
                        
                        // Toggle based on state
                        for (var i = 0; i < contents.length; i++) {
                            contents[i].style.display = anyHidden ? "block" : "none";
                        }
                        
                        expandAllButton.textContent = anyHidden ? "Collapse All" : "Expand All";
                    }
                </script>
            </div>
        </body>
        </html>
        """

        return html

    def _render_project(self, project: Dict[str, Any], status: str) -> str:
        """Render a project section for the HTML report."""
        project_id = f"{status}-{project['project'].replace(' ', '-')}"

        html = f"""
        <div class="project {status}">
            <div class="project-header">
                <h3 class="collapsible" onclick="toggleContent('{project_id}')">{project["project"]}</h3>
                <p>Location: <a href="{project["project_location"]}" target="_blank">{project["project_location"]}</a></p>
            </div>
            <div id="{project_id}" class="content">
                <h4>Diagnostics</h4>
        """

        # Group diagnostics by files
        diagnostics_by_file = self._group_diagnostics_by_file(
            project.get("diagnostics", [])
        )

        for file_path, diagnostics in diagnostics_by_file.items():
            file_id = f"{project_id}-{file_path.replace('/', '-')}"
            html += f"""
            <div class="file">
                <div class="file-header">
                    <h4 class="collapsible" onclick="toggleContent('{file_id}')">{file_path}</h4>
                </div>
                <div id="{file_id}" class="content">
            """

            # Group diagnostics by line
            diagnostics_by_line = self._group_diagnostics_by_line(diagnostics)

            for line_num, line_diagnostics in sorted(diagnostics_by_line.items()):
                html += """
                <div class="line">
                """

                for diag in line_diagnostics:
                    level_class = "error" if diag["level"] == "error" else "warning"
                    html += f"""
                    <div class="diagnostic {status}">
                        <span class="{level_class}">[{diag["level"]}]</span> {diag["lint_name"]} - 
                        <a href="{diag.get("github_ref", "")}" target="_blank">{file_path}:{diag["line"]}:{diag["column"]}</a> - 
                        {diag["message"]}
                    </div>
                    """

                html += "</div>"  # Close line div

            html += """
                </div>
            </div>
            """  # Close file content and file div

        html += """
            </div>
        </div>
        """  # Close project content and project div

        return html

    def _render_modified_project(self, project: Dict[str, Any]) -> str:
        """Render a modified project section for the HTML report."""
        project_id = f"modified-{project['project'].replace(' ', '-')}"

        html = f"""
        <div class="project modified">
            <div class="project-header">
                <h3 class="collapsible" onclick="toggleContent('{project_id}')">{project["project"]}</h3>
                <p>Location: <a href="{project["project_location"]}" target="_blank">{project["project_location"]}</a></p>
            </div>
            <div id="{project_id}" class="content">
        """

        diffs = project["diffs"]

        # Added Files
        if diffs["added_files"]:
            for file_data in diffs["added_files"]:
                file_id = f"{project_id}-added-{file_data['path'].replace('/', '-')}"
                html += f"""
                <div class="file added">
                    <div class="file-header">
                        <h4 class="collapsible" onclick="toggleContent('{file_id}')">{file_data["path"]}</h4>
                    </div>
                    <div id="{file_id}" class="content">
                """

                # Group diagnostics by line
                diagnostics_by_line = self._group_diagnostics_by_line(
                    file_data["diagnostics"]
                )

                for line_num, diagnostics in sorted(diagnostics_by_line.items()):
                    html += """
                    <div class="line">
                    """

                    for diag in diagnostics:
                        level_class = "error" if diag["level"] == "error" else "warning"
                        html += f"""
                        <div class="diagnostic added">
                            <span class="{level_class}">[{diag["level"]}]</span> {diag["lint_name"]} - 
                            <a href="{diag.get("github_ref", "")}" target="_blank">{file_data["path"]}:{diag["line"]}:{diag["column"]}</a> - 
                            {diag["message"]}
                        </div>
                        """

                    html += "</div>"  # Close line div

                html += """
                    </div>
                </div>
                """  # Close file content and file div

        # Removed Files
        if diffs["removed_files"]:
            for file_data in diffs["removed_files"]:
                file_id = f"{project_id}-removed-{file_data['path'].replace('/', '-')}"
                html += f"""
                <div class="file removed">
                    <div class="file-header">
                        <h4 class="collapsible" onclick="toggleContent('{file_id}')">{file_data["path"]}</h4>
                    </div>
                    <div id="{file_id}" class="content">
                """

                # Group diagnostics by line
                diagnostics_by_line = self._group_diagnostics_by_line(
                    file_data["diagnostics"]
                )

                for line_num, diagnostics in sorted(diagnostics_by_line.items()):
                    html += """
                    <div class="line">
                    """

                    for diag in diagnostics:
                        level_class = "error" if diag["level"] == "error" else "warning"
                        html += f"""
                        <div class="diagnostic removed">
                            <span class="{level_class}">[{diag["level"]}]</span> {diag["lint_name"]} - 
                            <a href="{diag.get("github_ref", "")}" target="_blank">{file_data["path"]}:{diag["line"]}:{diag["column"]}</a> - 
                            {diag["message"]}
                        </div>
                        """

                    html += "</div>"  # Close line div

                html += """
                    </div>
                </div>
                """  # Close file content and file div

        # Modified Files
        if diffs["modified_files"]:
            for file_data in diffs["modified_files"]:
                file_id = f"{project_id}-modified-{file_data['path'].replace('/', '-')}"
                html += f"""
                <div class="file modified">
                    <div class="file-header">
                        <h4 class="collapsible" onclick="toggleContent('{file_id}')">{file_data["path"]}</h4>
                    </div>
                    <div id="{file_id}" class="content">
                """

                file_diffs = file_data["diffs"]

                # Added Lines
                if file_diffs["added_lines"]:
                    for line_data in file_diffs["added_lines"]:
                        html += """
                        <div class="line added">
                        """

                        for diag in line_data["diagnostics"]:
                            level_class = (
                                "error" if diag["level"] == "error" else "warning"
                            )
                            html += f"""
                            <div class="diagnostic added">
                                <span class="{level_class}">[{diag["level"]}]</span> {diag["lint_name"]} - 
                                <a href="{diag.get("github_ref", "")}" target="_blank">{file_data["path"]}:{diag["line"]}:{diag["column"]}</a> - 
                                {diag["message"]}
                            </div>
                            """

                        html += "</div>"  # Close line div

                # Removed Lines
                if file_diffs["removed_lines"]:
                    for line_data in file_diffs["removed_lines"]:
                        html += """
                        <div class="line removed">
                        """

                        for diag in line_data["diagnostics"]:
                            level_class = (
                                "error" if diag["level"] == "error" else "warning"
                            )
                            html += f"""
                            <div class="diagnostic removed">
                                <span class="{level_class}">[{diag["level"]}]</span> {diag["lint_name"]} - 
                                <a href="{diag.get("github_ref", "")}" target="_blank">{file_data["path"]}:{diag["line"]}:{diag["column"]}</a> - 
                                {diag["message"]}
                            </div>
                            """

                        html += "</div>"  # Close line div

                # Modified Lines
                if file_diffs["modified_lines"]:
                    for line_data in file_diffs["modified_lines"]:
                        html += """
                        <div class="line modified">
                        """

                        # Removed diagnostics
                        for diag in line_data["removed"]:
                            level_class = (
                                "error" if diag["level"] == "error" else "warning"
                            )
                            html += f"""
                            <div class="diagnostic removed">
                                <span class="{level_class}">[{diag["level"]}]</span> {diag["lint_name"]} - 
                                <a href="{diag.get("github_ref", "")}" target="_blank">{file_data["path"]}:{diag["line"]}:{diag["column"]}</a> - 
                                {diag["message"]}
                            </div>
                            """

                        # Added diagnostics
                        for diag in line_data["added"]:
                            level_class = (
                                "error" if diag["level"] == "error" else "warning"
                            )
                            html += f"""
                            <div class="diagnostic added">
                                <span class="{level_class}">[{diag["level"]}]</span> {diag["lint_name"]} - 
                                <a href="{diag.get("github_ref", "")}" target="_blank">{file_data["path"]}:{diag["line"]}:{diag["column"]}</a> - 
                                {diag["message"]}
                            </div>
                            """

                        # Text diffs
                        # if line_data["text_diffs"]:
                        #     html += "<h6>Detailed Diffs</h6>"
                        #     for diff_data in line_data["text_diffs"]:
                        #         html += "<div class='text-diff'>"
                        #         for diff_line in diff_data["diff"]:
                        #             css_class = ""
                        #             if diff_line.startswith("- "):
                        #                 css_class = "diff-removed"
                        #             elif diff_line.startswith("+ "):
                        #                 css_class = "diff-added"

                        #             html += f"<pre class='diff-line {css_class}'>{diff_line}</pre>"
                        #         html += "</div>"

                        html += "</div>"  # Close line div

                html += """
                    </div>
                </div>
                """  # Close file content and file div

        html += """
            </div>
        </div>
        """  # Close project content and project div

        return html

    def save_json_diff(self, output_path: str) -> None:
        """Save the computed diffs as a JSON file."""
        with open(output_path, "w") as f:
            json.dump(self.diffs, f, indent=2)

        print(f"JSON diff saved to: {output_path}")


def main():
    """Main function to parse arguments and run the diff tool."""
    parser = argparse.ArgumentParser(
        description="Generate a diff report of diagnostic data between two JSON files."
    )
    parser.add_argument("old_file", help="Path to the old JSON file")
    parser.add_argument("new_file", help="Path to the new JSON file")
    parser.add_argument(
        "--html",
        help="Path to save the HTML report",
        default="diagnostic_diff_report.html",
    )
    parser.add_argument(
        "--json", help="Path to save the JSON diff data", default="diagnostic_diff.json"
    )

    args = parser.parse_args()

    diff_tool = DiagnosticDiff(args.old_file, args.new_file)
    diff_tool.generate_html_report(args.html)
    diff_tool.save_json_diff(args.json)


if __name__ == "__main__":
    main()

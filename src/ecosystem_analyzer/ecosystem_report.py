"""Render searchable HTML reports for ecosystem diagnostics."""

import json
import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, PackageLoader

from .schema import Diagnostic, ReportDiagnostic, RunData, RunOutput

logger = logging.getLogger(__name__)


def _report_diagnostic(output: RunOutput, diagnostic: Diagnostic) -> ReportDiagnostic:
    report_diagnostic: ReportDiagnostic = {
        "project": output["project"],
        "project_location": output.get("project_location", ""),
        "path": diagnostic["path"],
        "line": diagnostic["line"],
        "column": diagnostic["column"],
        "level": diagnostic["level"],
        "lint_name": diagnostic["lint_name"],
        "message": diagnostic["message"],
        "is_flaky": False,
        "flaky_runs": output["flaky_runs"],
        "variants": [],
    }
    if github_ref := diagnostic.get("github_ref"):
        report_diagnostic["github_ref"] = github_ref
    if "strict_settings" in output:
        report_diagnostic["strict_settings"] = output["strict_settings"]
    return report_diagnostic


def process_diagnostics(
    data: RunData, max_diagnostics_per_project: int | None = None
) -> list[ReportDiagnostic]:
    """Process the JSON data to extract all diagnostics (stable and flaky)."""
    all_diagnostics: list[ReportDiagnostic] = []

    total_diagnostics = 0
    for output in data["outputs"]:
        project = output["project"]

        # Count stable + flaky locations for the per-project limit
        num_stable = len(output["diagnostics"])
        num_flaky_locs = len(output["flaky_diagnostics"])
        num_diagnostics = num_stable + num_flaky_locs

        if (
            max_diagnostics_per_project is not None
            and num_diagnostics > max_diagnostics_per_project
        ):
            logger.info(
                f"Skipping project '{project}' ({num_diagnostics} diagnostics, limit: {max_diagnostics_per_project})"
            )
            continue

        total_diagnostics += num_diagnostics

        # Add stable diagnostics
        for diagnostic in output["diagnostics"]:
            all_diagnostics.append(_report_diagnostic(output, diagnostic))

        # Add flaky locations as entries with flaky metadata
        for loc in output["flaky_diagnostics"]:
            first_diagnostic = loc["variants"][0]["diagnostic"]
            entry = _report_diagnostic(output, first_diagnostic)
            entry["is_flaky"] = True
            entry["variants"] = loc["variants"]
            all_diagnostics.append(entry)

    logger.info(f"Total diagnostics included: {total_diagnostics}")

    return all_diagnostics


def generate_html_report(
    diagnostics: list[ReportDiagnostic],
    ty_commit: str,
    output_path: str | Path,
    flaky_project_names: set[str] | None = None,
) -> str | Path:
    """Generate an HTML report using Jinja2 template."""
    if flaky_project_names is None:
        flaky_project_names = set()

    project_strictness = {
        diagnostic["project"]: diagnostic["strict_settings"]
        for diagnostic in diagnostics
        if "strict_settings" in diagnostic
    }
    all_projects = sorted({d["project"] for d in diagnostics})
    lints = sorted({d["lint_name"] for d in diagnostics})
    levels = sorted({d["level"] for d in diagnostics})

    projects = []
    for project in all_projects:
        count = sum(1 for d in diagnostics if d["project"] == project)
        is_flaky = project in flaky_project_names
        projects.append((project, count, is_flaky))

    # Sort: flaky projects first, then by name
    projects.sort(key=lambda x: (not x[2], x[0]))

    lints = [
        (lint, sum(1 for d in diagnostics if d["lint_name"] == lint)) for lint in lints
    ]
    lints = sorted(lints, key=lambda x: x[1], reverse=True)
    levels = [
        (level, sum(1 for d in diagnostics if d["level"] == level)) for level in levels
    ]

    sorted_flaky_project_names = sorted(flaky_project_names)

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

    template = env.get_template("ecosystem_report.html")

    html_content = template.render(
        diagnostics=diagnostics,
        projects=projects,
        lints=lints,
        levels=levels,
        ty_commit=ty_commit,
        flaky_project_names=sorted_flaky_project_names,
        project_strictness=project_strictness,
    )

    # Write output file
    Path(output_path).write_text(html_content)

    return output_path


def generate(
    diagnostics_path: str | Path,
    output_path: str | Path,
    max_diagnostics_per_project: int | None = None,
) -> None:
    """Convert saved ecosystem diagnostics into a searchable HTML report."""

    diagnostics_path = Path(diagnostics_path)
    output_path = Path(output_path)

    with open(diagnostics_path) as f:
        data: RunData = json.load(f)
    diagnostics = process_diagnostics(data, max_diagnostics_per_project)

    ty_commits = {
        ty_commit
        for output in data["outputs"]
        if (ty_commit := output.get("ty_commit"))
    }
    if len(ty_commits) > 1:
        raise RuntimeError(
            "Error: The JSON file must contain diagnostics from a single ty commit."
        )
    ty_commit = ty_commits.pop() if ty_commits else "unknown"

    flaky_project_names = set()
    for output in data["outputs"]:
        if output["flaky_diagnostics"]:
            flaky_project_names.add(output["project"])

    output_file = generate_html_report(
        diagnostics, ty_commit, output_path, flaky_project_names
    )

    logger.info(f"Report generated successfully: {output_file}")

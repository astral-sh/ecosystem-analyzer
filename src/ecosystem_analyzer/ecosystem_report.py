"""Render searchable HTML reports for ecosystem diagnostics."""

import json
import logging
from collections import Counter
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, PackageLoader

from .schema import Diagnostic, ReportDiagnostic, RunData, RunOutput

logger = logging.getLogger(__name__)


def _is_http_url(value: object) -> bool:
    """Allow only explicit HTTP(S) links in reports rendered from diagnostic JSON."""
    return isinstance(value, str) and value.lower().startswith(("https://", "http://"))


def _report_diagnostic(output: RunOutput, diagnostic: Diagnostic) -> ReportDiagnostic:
    location = output.get("project_location", "")
    report_diagnostic: ReportDiagnostic = {
        "project": output["project"],
        "project_location": location if _is_http_url(location) else "",
        "strict_settings": output["strict_settings"],
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
    if (github_ref := diagnostic.get("github_ref")) and _is_http_url(github_ref):
        report_diagnostic["github_ref"] = github_ref
    return report_diagnostic


def process_diagnostics(data: RunData) -> list[ReportDiagnostic]:
    """Process the JSON data to extract all diagnostics (stable and flaky)."""
    all_diagnostics: list[ReportDiagnostic] = []

    for output in data["outputs"]:
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

    logger.info(f"Total diagnostics included: {len(all_diagnostics)}")

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
    }
    project_counts = Counter(d["project"] for d in diagnostics)
    projects = sorted(
        (
            (project, count, project in flaky_project_names)
            for project, count in project_counts.items()
        ),
        key=lambda item: (not item[2], item[0]),
    )
    lints = Counter(d["lint_name"] for d in diagnostics).most_common()
    levels = sorted(Counter(d["level"] for d in diagnostics).items())

    sorted_flaky_project_names = sorted(flaky_project_names)

    # Set up Jinja2 environment with package loader
    try:
        # Try PackageLoader first (works for installed packages)
        env = Environment(
            loader=PackageLoader("ecosystem_analyzer", "templates"), autoescape=True
        )
    except (ImportError, FileNotFoundError):
        # Fallback to FileSystemLoader for development
        template_path = Path(__file__).parent.parent.parent / "templates"
        if not template_path.exists():
            template_path = Path("templates")
        env = Environment(loader=FileSystemLoader(str(template_path)), autoescape=True)

    env.policies["json.dumps_kwargs"] = {"sort_keys": False, "separators": (",", ":")}
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
) -> None:
    """Convert saved ecosystem diagnostics into a searchable HTML report."""

    diagnostics_path = Path(diagnostics_path)
    output_path = Path(output_path)

    with open(diagnostics_path) as f:
        data: RunData = json.load(f)  # ty: ignore[unsound-assignment]
    diagnostics = process_diagnostics(data)

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

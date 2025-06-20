import json
import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, PackageLoader
from importlib.resources import files


def process_diagnostics(data):
    """Process the JSON data to extract all diagnostics."""
    all_diagnostics = []

    total_diagnostics = 0
    for output in data["outputs"]:
        project = output["project"]

        num_diagnostics = len(output["diagnostics"])
        if num_diagnostics > 1000:
            logging.info(
                f"Skipping project '{project}' ({num_diagnostics} diagnostics)"
            )
            continue

        total_diagnostics += num_diagnostics

        for diagnostic in output.get("diagnostics", []):
            # Add project metadata to each diagnostic for easier sorting/filtering
            diagnostic["project"] = project
            diagnostic["project_location"] = output.get("project_location")
            all_diagnostics.append(diagnostic)

    logging.info(f"Total diagnostics included: {total_diagnostics}")

    return all_diagnostics


def generate_html_report(diagnostics, ty_commit, output_path):
    """Generate an HTML report using Jinja2 template."""
    projects = sorted(set(d["project"] for d in diagnostics))
    lints = sorted(set(d["lint_name"] for d in diagnostics))
    levels = sorted(set(d["level"] for d in diagnostics))

    projects = [
        (project, sum(1 for d in diagnostics if d["project"] == project))
        for project in projects
    ]
    lints = [
        (lint, sum(1 for d in diagnostics if d["lint_name"] == lint)) for lint in lints
    ]
    lints = sorted(lints, key=lambda x: x[1], reverse=True)
    levels = [
        (level, sum(1 for d in diagnostics if d["level"] == level)) for level in levels
    ]

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
    )

    # Write output file
    with open(output_path, "w") as f:
        f.write(html_content)

    return output_path


def generate(diagnostics_path: str | Path, output_path: str | Path) -> str:
    diagnostics_path = Path(diagnostics_path)
    output_path = Path(output_path)

    with open(diagnostics_path) as f:
        data = json.load(f)
    diagnostics = process_diagnostics(data)

    ty_commits = set(output.get("ty_commit") for output in data["outputs"] if output.get("ty_commit"))
    if len(ty_commits) > 1:
        raise RuntimeError(
            "Error: The JSON file must contain diagnostics from a single ty commit."
        )
    ty_commit = ty_commits.pop() if ty_commits else "unknown"

    output_file = generate_html_report(diagnostics, ty_commit, output_path)

    logging.info(f"Report generated successfully: {output_file}")

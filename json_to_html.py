import argparse
import json
import sys

from jinja2 import Environment, FileSystemLoader


def load_json_data(file_path):
    """Load diagnostic data from a JSON file."""
    try:
        with open(file_path, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading JSON file: {e}", file=sys.stderr)
        sys.exit(1)


def process_diagnostics(data):
    """Process the JSON data to extract all diagnostics."""
    all_diagnostics = []

    total_diagnostics = 0
    for output in data["outputs"]:
        project = output["project"]

        num_diagnostics = len(output["diagnostics"])
        if num_diagnostics > 1000:
            print(f"Skipping project '{project}' ({num_diagnostics} diagnostics)")
            continue

        total_diagnostics += num_diagnostics

        for diagnostic in output.get("diagnostics", []):
            # Add project metadata to each diagnostic for easier sorting/filtering
            diagnostic["project"] = project
            diagnostic["project_location"] = output["project_location"]
            all_diagnostics.append(diagnostic)

    print(f"Total diagnostics included: {total_diagnostics}")

    return all_diagnostics


def generate_html_report(diagnostics, red_knot_commit, template_path, output_path):
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

    env = Environment(loader=FileSystemLoader("templates"))
    template = env.get_template(template_path)

    html_content = template.render(
        diagnostics=diagnostics,
        projects=projects,
        lints=lints,
        levels=levels,
        red_knot_commit=red_knot_commit,
    )

    # Write output file
    with open(output_path, "w") as f:
        f.write(html_content)

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Generate HTML report from diagnostic JSON data"
    )
    parser.add_argument("input_file", help="Path to input JSON file")
    parser.add_argument(
        "-o",
        "--output",
        default="ecosystem_report.html",
        help="Output HTML file path",
    )
    args = parser.parse_args()

    data = load_json_data(args.input_file)
    diagnostics = process_diagnostics(data)

    red_knot_commits = set(output["red_knot_commit"] for output in data["outputs"])
    if len(red_knot_commits) != 1:
        print(
            "Error: The JSON file must contain diagnostics from a single Red Knot commit.",
            file=sys.stderr,
        )
        sys.exit(1)
    red_knot_commit = red_knot_commits.pop()

    output_file = generate_html_report(
        diagnostics, red_knot_commit, "ecosystem_report.html", args.output
    )

    print(f"Report generated successfully: {output_file}")


if __name__ == "__main__":
    main()

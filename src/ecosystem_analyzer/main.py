import json
import logging
import sys
from pathlib import Path

import click
from .diagnostic import DiagnosticsParser
from .diff import DiagnosticDiff
from .ecosystem_report import generate
from .git import get_latest_ty_commits, resolve_ty_repo
from .manager import Manager


def setup_logging(verbose: bool = False) -> None:
    """Configure logging for the application."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--repository",
    help="Path to the ty repository",
    type=click.Path(exists=True, dir_okay=True, readable=True),
    required=False,
)
@click.option(
    "--target",
    help="Custom Rust target directory to use",
    type=click.Path(file_okay=False, dir_okay=True, readable=True, path_type=Path),
    required=False,
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Enable verbose logging",
)
@click.option(
    "--max-flaky-runs",
    help="Maximum number of times to run ty for flaky detection (1 = no detection, stops early if stable)",
    type=int,
    default=1,
)
@click.pass_context
def cli(ctx: click.Context, repository: str | None, target: Path | None, verbose: bool, max_flaky_runs: int) -> None:
    """
    Command-line interface for analyzing Python projects with ty.
    """
    ctx.ensure_object(dict)
    ctx.obj["repository"] = resolve_ty_repo(repository) if repository else None
    ctx.obj["target"] = target
    ctx.obj["verbose"] = verbose
    ctx.obj["max_flaky_runs"] = max_flaky_runs
    setup_logging(verbose)


@cli.command()
@click.option(
    "--project-name",
    help="Name of the project to analyze",
    type=str,
    required=True,
)
@click.option(
    "--commit",
    help="ty commit",
    type=str,
    default="origin/main",
)
@click.option(
    "--output",
    "-o",
    default="project-diagnostics.json",
    help="Output JSON file with collected diagnostics",
    type=click.Path(),
)
@click.option(
    "--profile",
    help="Cargo profile to use for building ty (e.g., 'dev', 'release')",
    type=str,
    default="dev",
)
@click.pass_context
def run(ctx, project_name: str, commit: str, output: str, profile: str) -> None:
    """
    Run ty on a specific project.
    """
    if ctx.obj["repository"] is None:
        click.echo("Error: --repository is required for this command", err=True)
        ctx.exit(1)

    manager = Manager(
        ty_repo=ctx.obj["repository"],
        target_dir=ctx.obj["target"],
        project_names=[project_name],
        profile=profile,
        max_flaky_runs=ctx.obj["max_flaky_runs"],
    )
    run_outputs = manager.run_for_commit(commit)
    manager.write_run_outputs(run_outputs, output)


@cli.command()
@click.option(
    "--commit",
    help="ty commit",
    type=str,
    default="origin/main",
)
@click.option(
    "--projects",
    help="List to a file with projects to analyze",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    required=True,
)
@click.option(
    "--output",
    "-o",
    default="ecosystem-diagnostics.json",
    help="Output JSON file with collected diagnostics",
    type=click.Path(),
)
@click.option(
    "--profile",
    help="Cargo profile to use for building ty (e.g., 'dev', 'release')",
    type=str,
    default="dev",
)
@click.pass_context
def analyze(ctx, commit: str, projects: str, output: str, profile: str) -> None:
    """
    Analyze Python ecosystem projects with ty and collect diagnostics.
    """
    if ctx.obj["repository"] is None:
        click.echo("Error: --repository is required for this command", err=True)
        ctx.exit(1)

    project_names = Path(projects).read_text().splitlines()

    manager = Manager(
        ty_repo=ctx.obj["repository"],
        target_dir=ctx.obj["target"],
        project_names=project_names,
        profile=profile,
        max_flaky_runs=ctx.obj["max_flaky_runs"],
    )
    run_outputs = manager.run_for_commit(commit)
    manager.write_run_outputs(run_outputs, output)


@cli.command()
@click.option(
    "--projects-old",
    help="List to a file with projects to analyze for old commit",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    required=True,
)
@click.option(
    "--projects-new",
    help="List to a file with projects to analyze for new commit",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    required=True,
)
@click.option(
    "--old",
    help="The old commit",
    type=str,
    required=True,
)
@click.option(
    "--new",
    help="The new commit",
    type=str,
    required=True,
)
@click.option(
    "--output-old",
    help="Output filename for old commit diagnostics",
    type=str,
    default="diagnostics-old.json",
)
@click.option(
    "--output-new",
    help="Output filename for new commit diagnostics",
    type=str,
    default="diagnostics-new.json",
)
@click.option(
    "--profile",
    help="Cargo profile to use for building ty (e.g., 'dev', 'release')",
    type=str,
    default="dev",
)
@click.pass_context
def diff(
    ctx,
    projects_old: str,
    projects_new: str,
    old: str,
    new: str,
    output_old: str,
    output_new: str,
    profile: str,
) -> None:
    """
    Compare diagnostics between two commits.
    """
    if ctx.obj["repository"] is None:
        click.echo("Error: --repository is required for this command", err=True)
        ctx.exit(1)

    project_names_old = Path(projects_old).read_text().splitlines()
    project_names_new = Path(projects_new).read_text().splitlines()

    # Create union of both project lists for installation
    all_project_names = list(set(project_names_old + project_names_new))

    manager = Manager(
        ty_repo=ctx.obj["repository"],
        target_dir=ctx.obj["target"],
        project_names=all_project_names,
        profile=profile,
        max_flaky_runs=ctx.obj["max_flaky_runs"],
    )

    # Run for old commit with old projects
    manager.activate(project_names_old)
    run_outputs_old = manager.run_for_commit(old)
    manager.write_run_outputs(run_outputs_old, output_old)

    # Run for new commit with new projects
    manager.activate(project_names_new)
    run_outputs_new = manager.run_for_commit(new)
    manager.write_run_outputs(run_outputs_new, output_new)


@cli.command()
@click.argument(
    "old_file",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    required=True,
)
@click.argument(
    "new_file",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    required=True,
)
@click.option(
    "--output-html",
    type=click.Path(writable=True),
    default="diff.html",
    help="Path for the standalone HTML diff report",
)
@click.option(
    "--output-json",
    type=click.Path(writable=True),
    help="Path to save the JSON diff data",
)
@click.option(
    "--old-name",
    type=str,
    help="Label for the old version (e.g., branch name, commit, or description)",
)
@click.option(
    "--new-name",
    type=str,
    help="Label for the new version (e.g., branch name, commit, or description)",
)
def generate_diff(
    old_file: str,
    new_file: str,
    output_html: str,
    output_json: str | None,
    old_name: str | None,
    new_name: str | None,
) -> None:
    """
    Generate a diff report of diagnostic data between two JSON files.

    OLD_FILE: Path to the old JSON file.
    NEW_FILE: Path to the new JSON file.
    """
    diff_tool = DiagnosticDiff(old_file, new_file, old_name=old_name, new_name=new_name)
    diff_tool.generate_html_report(output_html)
    if output_json:
        diff_tool.save_json_diff(output_json)


@cli.command()
@click.option(
    "--projects",
    help="List to a file with projects to analyze",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    required=True,
)
@click.option(
    "--num-commits",
    help="Number of recent commits to analyze",
    type=int,
    default=10,
)
@click.option(
    "output",
    "--output",
    help="Output JSON file with statistics",
    type=click.Path(),
    default="history-statistics.json",
)
@click.option(
    "--profile",
    help="Cargo profile to use for building ty (e.g., 'dev', 'release')",
    type=str,
    default="release",
)
@click.pass_context
def history(ctx, projects: str, num_commits: int, output: str, profile: str) -> None:
    """
    Analyze diagnostics across a range of commits.
    """
    if ctx.obj["repository"] is None:
        click.echo("Error: --repository is required for this command", err=True)
        ctx.exit(1)

    repository = ctx.obj["repository"]

    last_commits = get_latest_ty_commits(repository, num_commits)

    for commit in last_commits:
        message = commit.message.splitlines()[0]
        logging.debug(f"Found commit: {message}")

    project_names = Path(projects).read_text().splitlines()

    manager = Manager(
        ty_repo=repository,
        target_dir=ctx.obj["target"],
        project_names=project_names,
        profile=profile,
        max_flaky_runs=ctx.obj["max_flaky_runs"],
    )

    statistics = []

    for idx, commit in enumerate(last_commits):
        message = commit.message.splitlines()[0]
        sha = commit.hexsha[:7]
        logging.debug(f"Analyzing commit: {message}")

        run_outputs = manager.run_for_commit(commit)
        manager.write_run_outputs(run_outputs, f"history-diagnostics-{idx}-{sha}.json")

        total_diagnostics = sum(len(output["diagnostics"]) for output in run_outputs)

        logging.info(f"Total diagnostics for commit '{sha}': {total_diagnostics}")

        statistics.append(
            {
                "commit": sha,
                "commit_message": message,
                "total_diagnostics": total_diagnostics,
            }
        )

    with Path(output).open("w") as json_file:
        json.dump({"statistics": statistics}, json_file)


@cli.command()
@click.argument(
    "old_file",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    required=True,
)
@click.argument(
    "new_file",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    required=True,
)
@click.option(
    "--output-html",
    type=click.Path(writable=True),
    default="timing-diff.html",
    help="Path for the HTML timing diff report",
)
@click.option(
    "--old-name",
    type=str,
    help="Label for the old version (e.g., branch name, commit, or description)",
)
@click.option(
    "--new-name",
    type=str,
    help="Label for the new version (e.g., branch name, commit, or description)",
)
def generate_timing_diff(
    old_file: str,
    new_file: str,
    output_html: str,
    old_name: str | None,
    new_name: str | None,
) -> None:
    """
    Generate a timing diff report comparing execution times between two JSON files.

    OLD_FILE: Path to the old JSON file.
    NEW_FILE: Path to the new JSON file.
    """
    diff_tool = DiagnosticDiff(old_file, new_file, old_name=old_name, new_name=new_name)
    diff_tool.generate_timing_html_report(output_html)


@cli.command()
@click.argument(
    "old_file",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    required=True,
)
@click.argument(
    "new_file",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    required=True,
)
@click.option(
    "--output",
    type=click.Path(writable=True),
    default="diff-statistics.md",
    help="Path for the Markdown statistics file",
)
@click.option(
    "--old-name",
    type=str,
    help="Label for the old version (e.g., branch name, commit, or description)",
)
@click.option(
    "--new-name",
    type=str,
    help="Label for the new version (e.g., branch name, commit, or description)",
)
def generate_diff_statistics(
    old_file: str,
    new_file: str,
    output: str,
    old_name: str | None,
    new_name: str | None,
) -> None:
    """
    Generate a Markdown statistics report of diagnostic differences between two JSON files.

    OLD_FILE: Path to the old JSON file.
    NEW_FILE: Path to the new JSON file.
    """
    diff = DiagnosticDiff(old_file, new_file, old_name=old_name, new_name=new_name)
    statistics = diff._calculate_statistics()
    failed_projects = diff.diffs.get("failed_projects", [])

    markdown_content = ""

    # Add failed projects section if any
    if failed_projects:
        markdown_content += "**Failing projects**:\n\n"
        markdown_content += "| Project | Old Status | New Status | Old Return Code | New Return Code |\n"
        markdown_content += "|---------|------------|------------|-----------------|------------------|\n"

        for project in failed_projects:
            old_status = project["old_status"]
            new_status = project["new_status"]
            old_rc = project.get("old_return_code", "None")
            new_rc = project.get("new_return_code", "None")

            markdown_content += f"| `{project['project']}` | {old_status} | {new_status} | `{old_rc}` | `{new_rc}` |\n"

        markdown_content += "\n"

    # Add diagnostic changes section
    if (
        statistics["total_added"] == 0
        and statistics["total_removed"] == 0
        and statistics["total_changed"] == 0
    ):
        markdown_content += "No diagnostic changes detected ✅"
    else:
        if failed_projects:
            markdown_content += "**Diagnostic changes:**\n"

        markdown_content += """
| Lint rule | Added | Removed | Changed |
|-----------|------:|--------:|--------:|
"""

        for lint_data in statistics["merged_by_lint"]:
            markdown_content += f"| `{lint_data['lint_name']}` | {lint_data['added']:,} | {lint_data['removed']:,} | {lint_data['changed']:,} |\n"

        markdown_content += f"| **Total** | **{statistics['total_added']:,}** | **{statistics['total_removed']:,}** | **{statistics['total_changed']:,}** |\n"

    with open(output, "w") as f:
        f.write(markdown_content)

    print(f"Markdown statistics report generated at: {output}")


@cli.command()
@click.argument(
    "diagnostics",
    type=click.Path(exists=True, dir_okay=False, readable=True),
)
@click.option(
    "--output",
    help="Path to the output HTML file",
    type=click.Path(),
    default="ecosystem-report.html",
)
@click.option(
    "--max-diagnostics-per-project",
    help="Maximum number of diagnostics per project to include (unlimited by default)",
    type=int,
    default=None,
)
def generate_report(
    diagnostics: str, output: str, max_diagnostics_per_project: int | None
) -> None:
    """
    Generate an HTML report from the diagnostics JSON file.
    """

    generate(diagnostics, output, max_diagnostics_per_project)


@cli.command()
@click.option(
    "--output",
    "-o",
    default="parsed-diagnostics.json",
    help="Output JSON file with parsed diagnostics",
    type=click.Path(),
)
@click.option(
    "--project-name",
    default="stdin",
    help="Project name for the output",
    type=str,
)
@click.option(
    "--project-location",
    help="GitHub URL for the project (for GitHub links)",
    type=str,
)
@click.option(
    "--commit",
    help="Commit hash for GitHub links",
    type=str,
)
def parse_diagnostics(
    output: str, project_name: str, project_location: str | None, commit: str | None
) -> None:
    """
    Parse ty diagnostic output from stdin and generate a JSON file.
    """
    # Read diagnostic output from stdin
    diagnostic_content = sys.stdin.read()

    if not diagnostic_content.strip():
        click.echo("No diagnostic content provided on stdin", err=True)
        return

    # Create a parser with the provided information
    parser = DiagnosticsParser(
        repo_location=project_location,
        repo_commit=commit,
        repo_working_dir=Path.cwd(),
    )

    # Parse the diagnostics (parser will conditionally include github_ref)
    diagnostics = parser.parse(diagnostic_content)

    # Create output structure - only include fields that have meaningful values
    run_output = {
        "project": project_name,
        "diagnostics": diagnostics,
    }

    # Only include optional fields if they're provided
    if project_location:
        run_output["project_location"] = project_location
    if commit:
        run_output["ty_commit"] = commit

    # Write to JSON file in the same format as Manager.write_run_outputs
    output_data = {"outputs": [run_output]}

    output_path = Path(output)
    with output_path.open("w") as json_file:
        json.dump(output_data, json_file, indent=4)

    logging.info(f"Parsed {len(diagnostics)} diagnostics and wrote to {output_path}")
    click.echo(f"Parsed {len(diagnostics)} diagnostics and wrote to {output_path}")


if __name__ == "__main__":
    cli()

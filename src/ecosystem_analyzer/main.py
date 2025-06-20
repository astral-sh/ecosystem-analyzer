import json
import logging
import sys
from pathlib import Path

import click
from git import Repo

from .diagnostic import DiagnosticsParser
from .diff import DiagnosticDiff
from .ecosystem_report import generate
from .git import get_latest_ty_commits
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
    "--verbose",
    "-v",
    is_flag=True,
    help="Enable verbose logging",
)
@click.pass_context
def cli(ctx: click.Context, repository: str | None, verbose: bool) -> None:
    """
    Command-line interface for analyzing Python projects with ty.
    """
    ctx.ensure_object(dict)
    ctx.obj["repository"] = Repo(repository) if repository else None
    ctx.obj["verbose"] = verbose
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
@click.pass_context
def run(ctx, project_name: str, commit: str, output: str) -> None:
    """
    Run ty on a specific project.
    """
    if ctx.obj["repository"] is None:
        click.echo("Error: --repository is required for this command", err=True)
        ctx.exit(1)

    manager = Manager(ty_repo=ctx.obj["repository"], project_names=[project_name])
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
@click.pass_context
def analyze(ctx, commit: str, projects: str, output: str) -> None:
    """
    Analyze Python ecosystem projects with ty and collect diagnostics.
    """
    if ctx.obj["repository"] is None:
        click.echo("Error: --repository is required for this command", err=True)
        ctx.exit(1)

    project_names = Path(projects).read_text().splitlines()

    manager = Manager(ty_repo=ctx.obj["repository"], project_names=project_names)
    run_outputs = manager.run_for_commit(commit)
    manager.write_run_outputs(run_outputs, output)


@cli.command()
@click.option(
    "--projects",
    help="List to a file with projects to analyze",
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
@click.pass_context
def diff(ctx, projects: str, old: str, new: str) -> None:
    """
    Compare diagnostics between two commits.
    """
    if ctx.obj["repository"] is None:
        click.echo("Error: --repository is required for this command", err=True)
        ctx.exit(1)

    project_names = Path(projects).read_text().splitlines()

    manager = Manager(ty_repo=ctx.obj["repository"], project_names=project_names)

    run_outputs_old = manager.run_for_commit(old)
    manager.write_run_outputs(
        run_outputs_old, f"diagnostics-old-{old.replace('/', '-')}.json"
    )

    run_outputs_new = manager.run_for_commit(new)
    manager.write_run_outputs(
        run_outputs_new, f"diagnostics-new-{new.replace('/', '-')}.json"
    )


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
@click.pass_context
def history(ctx, projects: str, num_commits: int, output: str) -> None:
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

    manager = Manager(ty_repo=repository, project_names=project_names, release=True)

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
@click.option(
    "--diagnostics",
    help="Path to the JSON file with diagnostics",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    required=True,
)
@click.option(
    "--output",
    help="Path to the output HTML file",
    type=click.Path(),
    default="ecosystem-report.html",
)
def generate_report(diagnostics: str, output: str) -> None:
    """
    Generate an HTML report from the diagnostics JSON file.
    """

    generate(diagnostics, output)


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

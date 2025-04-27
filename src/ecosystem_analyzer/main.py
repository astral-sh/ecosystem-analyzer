import json
import logging
from pathlib import Path

import click
from git import Repo

from .ecosystem_report import generate
from .git import get_latest_red_knot_commits
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
    help="Path to the Red Knot repository",
    type=click.Path(exists=True, dir_okay=True, readable=True),
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Enable verbose logging",
)
@click.pass_context
def cli(ctx: click.Context, repository: str, verbose: bool) -> None:
    """
    Command-line interface for analyzing Python projects with Red Knot.
    """
    ctx.ensure_object(dict)
    ctx.obj["repository"] = Repo(repository)
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
    help="Red Knot commit",
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
    Run Red Knot on a specific project.
    """

    manager = Manager(red_knot_repo=ctx.obj["repository"], project_names=[project_name])
    run_outputs = manager.run_for_commit(commit)
    manager.write_run_outputs(run_outputs, output)


@cli.command()
@click.option(
    "--commit",
    help="Red Knot commit",
    type=str,
    default="origin/main",
)
@click.option(
    "--projects",
    help="List to a file with projects to analyze",
    type=click.Path(exists=True, dir_okay=False, readable=True),
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
    Analyze Python ecosystem projects with Red Knot and collect diagnostics.
    """

    project_names = Path(projects).read_text().splitlines()

    manager = Manager(red_knot_repo=ctx.obj["repository"], project_names=project_names)
    run_outputs = manager.run_for_commit(commit)
    manager.write_run_outputs(run_outputs, output)


@cli.command()
@click.option(
    "--projects",
    help="List to a file with projects to analyze",
    type=click.Path(exists=True, dir_okay=False, readable=True),
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

    repository = ctx.obj["repository"]

    last_commits = get_latest_red_knot_commits(repository, num_commits)

    for commit in last_commits:
        message = commit.message.splitlines()[0]
        logging.debug(f"Found commit: {message}")

    project_names = Path(projects).read_text().splitlines()

    manager = Manager(red_knot_repo=repository, project_names=project_names)

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


if __name__ == "__main__":
    cli()

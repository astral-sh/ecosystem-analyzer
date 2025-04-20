import logging
from pathlib import Path

import click
from git import Repo

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
    required=True,
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
@click.pass_context
def run(ctx, project_name: str, commit: str) -> None:
    """
    Run Red Knot on a specific project.
    """

    manager = Manager(red_knot_repo=ctx.obj["repository"], project_names=[project_name])
    manager.run_for_commit(commit)
    manager.write_run_outputs(Path("output.json"))


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
    default="output.json",
    help="Output JSON file with collected diagnostics",
    type=click.Path(),
)
@click.pass_context
def analyze(ctx, commit: str, projects: str, output: str) -> None:
    """
    Analyze Python projects with Red Knot and generate statistics.
    """

    logging.info("Starting ecosystem analysis")

    project_names = Path(projects).read_text().splitlines()

    manager = Manager(red_knot_repo=ctx.obj["repository"], project_names=project_names)
    manager.run_for_commit(commit)
    manager.write_run_outputs(output)


if __name__ == "__main__":
    cli()

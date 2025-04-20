import logging
from pathlib import Path

import click
from git import Repo
from mypy_primer.model import Project
from mypy_primer.projects import get_projects

from .installed_project import InstalledProject
from .red_knot import RedKnot
from .run_output import write_run_output


def setup_logging(verbose: bool = False) -> None:
    """Configure logging for the application."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def get_available_projects() -> dict[str, Project]:
    available_projects: dict[str, Project] = {}
    for project in get_projects():
        project_name = (
            project.name_override
            if project.name_override
            else project.location.split("/")[-1]
        )

        available_projects[project_name] = project

    return available_projects


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

    projects = get_available_projects()
    try:
        project = projects[project_name]
    except KeyError as e:
        raise RuntimeError(f"Project {project_name} not found in available projects.") from e

    installed_project = InstalledProject(project)
    installed_project.install()

    repository = ctx.obj["repository"]
    red_knot = RedKnot(repository)
    red_knot.compile_for_commit(commit)
    run_output = red_knot.run_on_project(installed_project)

    write_run_output(run_output, Path("output.json"))


@cli.command()
@click.option(
    "--projects",
    help="List to a file with projects to analyze",
    type=click.Path(exists=True, dir_okay=False, readable=True),
)
@click.option(
    "--output",
    "-o",
    default="statistics.json",
    help="Output JSON file for statistics",
    type=click.Path(),
)
@click.pass_context
def analyze(ctx, projects: str, output: str) -> None:
    """
    Analyze Python projects with Red Knot and generate statistics.
    """

    repository = ctx.obj["repository"]

    logging.info("Starting ecosystem analysis")

    selected_projects = Path(projects).read_text().splitlines()

    available_projects = get_available_projects()

    unavailable_projects = set(selected_projects) - set(available_projects.keys())
    if unavailable_projects:
        raise RuntimeError(
            f"Projects {', '.join(unavailable_projects)} not found in available projects. "
        )

    installed_projects: list[InstalledProject] = []
    for project_name in selected_projects:
        project = available_projects[project_name]

        logging.info(f"Processing project: {project.location}")
        installed_project = InstalledProject(project)
        installed_project.install()

        installed_projects.append(installed_project)

    # red_knot = RedKnot(repository)
    # TODO


if __name__ == "__main__":
    cli()

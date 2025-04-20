import json
import logging
import re
from pathlib import Path
from typing import TypedDict

import click
from mypy_primer.projects import get_projects
from mypy_primer.model import Project

from .installed_project import InstalledProject
from .red_knot_manager import RedKnotManager


class ProjectStatistics(TypedDict):
    name: str
    diagnostics_count: int


class CommitStatistics(TypedDict):
    commit_message: str
    total_diagnostics: int
    projects: list[ProjectStatistics]


def setup_logging(verbose: bool = False) -> None:
    """Configure logging for the application."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def write_statistics_to_json(statistics: list[CommitStatistics], filename: str) -> None:
    # Write to JSON file
    with open(filename, "w") as json_file:
        json.dump(statistics, json_file, indent=4)
    logging.info(f"Statistics written to {filename}")


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def cli() -> None:
    """
    Ecosystem Analyzer CLI with subcommands.
    """
    pass


@cli.command()
@click.option(
    "--output",
    "-o",
    default="statistics.json",
    help="Output JSON file for statistics",
    type=click.Path(),
)
@click.option(
    "--projects",
    help="List to a file with projects to analyze",
    type=click.Path(exists=True, dir_okay=False, readable=True),
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Enable verbose logging",
)
def run(output: str, projects: str, verbose: bool) -> None:
    """
    Analyze Python projects with Red Knot and generate statistics.
    """
    setup_logging(verbose)
    logging.info("Starting ecosystem analysis")

    installed_projects: list[InstalledProject] = []

    selected_projects = Path(projects).read_text().splitlines()

    available_projects: dict[str, Project] = {}
    for project in get_projects():
        project_name = project.name_override if project.name_override else project.location.split("/")[-1]

        available_projects[project_name] = project

    for project_name in selected_projects:
        if project := available_projects.get(project_name):
            logging.info(f"Processing project: {project.location}")
            installed_project = InstalledProject(project)
            installed_project.install()

            installed_projects.append(installed_project)
        else:
            raise RuntimeError(
                f"Project {project_name} not found in available projects. "
            )

    manager = RedKnotManager(installed_projects)
    statistics = manager.run()

    write_statistics_to_json(statistics, output)


if __name__ == "__main__":
    cli()

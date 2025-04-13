import json
import logging
import re
from pathlib import Path
from typing import TypedDict

import click

from .config import PROJECT_PATTERN
from .ecosystem import load_ecosystem
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


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--output",
    "-o",
    default="statistics.json",
    help="Output JSON file for statistics",
    type=click.Path(),
)
@click.option(
    "--project-pattern",
    "-p",
    default=PROJECT_PATTERN,
    help="Custom project pattern regex",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Enable verbose logging",
)
def cli(output: str, project_pattern: str, verbose: bool) -> None:
    """
    Analyze Python projects with Red Knot and generate statistics.
    """
    setup_logging(verbose)
    logging.info("Starting ecosystem analysis")

    installed_projects: list[InstalledProject] = []

    for project in load_ecosystem():
        if re.search(project_pattern, project.location):
            logging.info(f"Processing project: {project.location}")
            installed_project = InstalledProject(project)
            installed_project.install()

            installed_projects.append(installed_project)

    manager = RedKnotManager(installed_projects)
    statistics = manager.run()

    write_statistics_to_json(statistics, output)


if __name__ == "__main__":
    cli()

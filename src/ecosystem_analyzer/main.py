import json
import re
from pathlib import Path

from mypy_primer.projects import get_projects

from .config import PROJECT_PATTERN
from .installed_project import InstalledProject
from .red_knot_manager import RedKnotManager


def write_statistics_to_json(statistics: list[tuple[str, int]], filename: str) -> None:
    # Convert the list of tuples to a list of dictionaries
    statistics_dict = [
        {"commit_message": msg, "diagnostics_count": count} for msg, count in statistics
    ]

    # Write to JSON file
    with open(filename, "w") as json_file:
        json.dump(statistics_dict, json_file, indent=4)


def main():
    installed_projects: list[InstalledProject] = []

    for project in get_projects():
        if re.search(PROJECT_PATTERN, project.location):
            print(f"Processing project: {project.location}")
            installed_project = InstalledProject(project)
            installed_project.install()

            installed_projects.append(installed_project)

    manager = RedKnotManager(installed_projects)
    statistics = manager.run()

    write_statistics_to_json(statistics, "statistics.json")


if __name__ == "__main__":
    main() 

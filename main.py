import os
import re
import subprocess
import tempfile
from pathlib import Path

from git import Commit, Repo
from mypy_primer.projects import get_projects

RUFF_REPO_PATH = "/home/shark/ruff3"

PROJECT_PATTERN = r"/(mypy_primer|black|pyp|git-revise|zipp|arrow|isort|itsdangerous|rich|packaging|pybind11|pyinstrument|typeshed-stats|scrapy|werkzeug|bidict|async-utils)$"
# PROJECT_PATTERN = r"/(arrow|black|rich)$"


LOG_FILE = "log.txt"


COMMIT_BLACKLIST = ["907b6ed7b57d58dd6a26488e1393106dba78cb2d"]

NUM_COMMITS = 60


class InstalledProject:
    def __init__(self, project) -> None:
        self.project = project
        self.temp_dir = tempfile.TemporaryDirectory()

    def _clone(self) -> None:
        try:
            print(f"Cloning {self.project.location} into {self.temp_dir.name}")
            Repo.clone_from(url=self.project.location, to_path=self.temp_dir.name)
        except Exception as e:
            print(f"Error cloning repository: {e}")
            return

    def _install_dependencies(self) -> None:
        if self.project.deps:
            print(f"Installing dependencies: {', '.join(self.project.deps)}")
            subprocess.run(["uv", "venv"], check=True, cwd=self.temp_dir.name)
            subprocess.run(
                ["uv", "pip", "install", "--link-mode=copy", *self.project.deps],
                check=True,
                cwd=self.temp_dir.name,
                capture_output=False,
            )

    def install(self) -> None:
        self._clone()
        self._install_dependencies()

    def count_diagnostics(self, red_knot: Path) -> int:
        extra_args = self.project.knot_paths if self.project.knot_paths else []
        result = subprocess.run(
            [
                red_knot.as_posix(),
                "check",
                "--output-format=concise",
                "--python",
                ".venv",
                *extra_args,
            ],
            cwd=self.temp_dir.name,
            check=False,
            capture_output=True,
            text=True,
        )

        # Append result.stdout to log file
        with open(LOG_FILE, "a") as log_file:
            log_file.write(result.stdout)

        return len(result.stdout.splitlines())


class RedKnotManager:
    def __init__(self, projects: list[InstalledProject]) -> None:
        self.last_commits = self._get_latest_red_knot_commits()
        self.projects = projects

    def _get_latest_red_knot_commits(self):
        repo = Repo(RUFF_REPO_PATH)
        repo.git.checkout("main")

        commits = []
        for commit in repo.iter_commits():
            # Check blacklist
            if commit.hexsha in COMMIT_BLACKLIST:
                continue

            if commit.message.startswith("[red-knot] "):
                commits.append(commit)
                if len(commits) >= NUM_COMMITS:
                    break

        commits.reverse()

        return commits

    def _compile_for_commit(self, commit: Commit) -> Path:
        # Checkout the commit
        repo = Repo(RUFF_REPO_PATH)
        repo.git.checkout(commit)

        # Compile Red Knot
        cargo_target_dir = Path(RUFF_REPO_PATH) / "target"

        env = os.environ.copy()
        env["CARGO_TARGET_DIR"] = cargo_target_dir.as_posix()

        subprocess.run(
            ["cargo", "build", "--package", "red_knot"],
            cwd=RUFF_REPO_PATH,
            capture_output=True,
            check=True,
            env=env,
        )

        executable = cargo_target_dir / "debug" / "red_knot"

        return executable

    def run(
        self,
    ):
        statistics = []
        for commit in self.last_commits:
            message = commit.message.splitlines()[0]
            print(message)

            executable = self._compile_for_commit(commit)

            total_diagnostics = 0

            for project in self.projects:
                total_diagnostics += project.count_diagnostics(executable)

            print(total_diagnostics)

            statistics.append((message, total_diagnostics))

        return statistics


def write_statistics_to_json(statistics: list[tuple[str, int]], filename: str) -> None:
    import json

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
    # read_from_json_and_plot("statistics.json")


if __name__ == "__main__":
    main()

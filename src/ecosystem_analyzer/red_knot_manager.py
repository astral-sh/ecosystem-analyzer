import logging
import os
import subprocess
from pathlib import Path

from git import Commit, Repo

from .config import NUM_COMMITS, RUFF_REPO_PATH
from .installed_project import InstalledProject


class RedKnotManager:
    def __init__(self, projects: list[InstalledProject]) -> None:
        self.last_commits = self._get_latest_red_knot_commits()
        self.projects = projects

    def _get_latest_red_knot_commits(self):
        repo = Repo(RUFF_REPO_PATH)
        repo.git.checkout("origin/main")

        commits = []
        for commit in repo.iter_commits():
            if commit.message.startswith("[red-knot] "):  # type: ignore
                commits.append(commit)
                if len(commits) >= NUM_COMMITS:
                    break

        commits.reverse()

        return commits

    def _compile_for_commit(self, commit: Commit) -> Path:
        # Checkout the commit
        repo = Repo(RUFF_REPO_PATH)
        logging.debug(f"Executing: git checkout {commit.hexsha}")
        repo.git.checkout(commit)

        # Compile Red Knot
        cargo_target_dir = Path(RUFF_REPO_PATH) / "target"

        env = os.environ.copy()
        env["CARGO_TARGET_DIR"] = cargo_target_dir.as_posix()

        logging.info(f"Compiling Red Knot for commit {commit.hexsha[:7]}")
        cargo_cmd = ["cargo", "build", "--package", "red_knot"]
        logging.debug(f"Executing: {' '.join(cargo_cmd)} (CARGO_TARGET_DIR={cargo_target_dir})")
        subprocess.run(
            cargo_cmd,
            cwd=RUFF_REPO_PATH,
            capture_output=True,
            check=True,
            env=env,
        )

        executable = cargo_target_dir / "debug" / "red_knot"

        return executable

    def run(self):
        statistics = []
        for commit in self.last_commits:
            message = commit.message.splitlines()[0]
            logging.info(f"Analyzing commit: {message}")

            executable = self._compile_for_commit(commit)

            total_diagnostics = 0
            project_stats = []

            for project in self.projects:
                diagnostics = project.count_diagnostics(executable)
                total_diagnostics += diagnostics
                project_stats.append({
                    "location": project.project.location,
                    "diagnostics_count": diagnostics,
                })

            logging.info(f"Total diagnostics: {total_diagnostics}")

            statistics.append({
                "commit_message": message,
                "total_diagnostics": total_diagnostics,
                "projects": project_stats,
            })

        return statistics 

import os
import subprocess
from pathlib import Path

from git import Commit, Repo

from .installed_project import InstalledProject

RUFF_REPO_PATH = "/home/shark/ruff3"
COMMIT_BLACKLIST = ["907b6ed7b57d58dd6a26488e1393106dba78cb2d"]
NUM_COMMITS = 1


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

    def run(self):
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

import logging
import os
import subprocess
from pathlib import Path

from git import Commit, Repo

from .config import LOG_FILE
from .diagnostic import DiagnosticsParser
from .installed_project import InstalledProject
from .run_output import RunOutput


class RedKnot:
    def __init__(self, repository: Repo) -> None:
        self.repository: Repo = repository
        self.working_dir: Path = Path(self.repository.working_dir)
        self.cargo_target_dir: Path = self.working_dir / "target"

    def compile_for_commit(self, commit: str | Commit):
        # Checkout the commit
        logging.debug(f"Checking out Red Knot commit '{commit}'")
        self.repository.git.checkout(commit)

        # Compile Red Knot
        env = os.environ.copy()
        env["CARGO_TARGET_DIR"] = self.cargo_target_dir.as_posix()

        logging.info("Compiling Red Knot")
        cargo_cmd = ["cargo", "build", "--package", "red_knot"]
        logging.debug(
            f"Executing: {' '.join(cargo_cmd)} (CARGO_TARGET_DIR={self.cargo_target_dir})"
        )
        subprocess.run(
            cargo_cmd,
            cwd=self.working_dir,
            capture_output=True,
            check=True,
            env=env,
        )

        self.executable = self.cargo_target_dir / "debug" / "red_knot"

    def run_on_project(self, project: InstalledProject) -> RunOutput:
        extra_args = project.paths
        cmd = [
            self.executable.as_posix(),
            "check",
            "--output-format=concise",
            "--python",
            ".venv",
            *extra_args,
        ]
        logging.debug(f"Executing: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            cwd=project.root_directory,
            check=False,
            capture_output=True,
            text=True,
        )

        # Append result.stdout to log file
        with open(LOG_FILE, "a") as log_file:
            log_file.write(result.stdout)

        parser = DiagnosticsParser(
            repo_location=project.location,
            repo_branch=project.default_branch,
            repo_working_dir=project.root_directory,
        )
        diagnostics = parser.parse(result.stdout)

        return {
            "project": project.name,
            "project_location": project.location,
            "red_knot_commit": self.repository.head.commit.hexsha,
            "diagnostics": diagnostics,
        }

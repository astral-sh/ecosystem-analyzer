import logging
import os
import subprocess
from pathlib import Path

from git import Commit, Repo

from .config import LOG_FILE
from .diagnostic import DiagnosticsParser
from .installed_project import InstalledProject
from .run_output import RunOutput


class Ty:
    def __init__(self, repository: Repo) -> None:
        self.repository: Repo = repository
        self.working_dir: Path = Path(self.repository.working_dir)
        self.cargo_target_dir: Path = self.working_dir / "target"

    def compile_for_commit(self, commit: str | Commit):
        # Checkout the commit
        logging.debug(f"Checking out ty commit '{commit}'")
        self.repository.git.checkout(commit)

        # Compile ty
        env = os.environ.copy()
        env["CARGO_TARGET_DIR"] = self.cargo_target_dir.as_posix()

        logging.info("Compiling ty")
        cargo_cmd = ["cargo", "build", "--package", "ty"]
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

        self.executable = self.cargo_target_dir / "debug" / "ty"

    def run_on_project(self, project: InstalledProject) -> RunOutput:
        logging.info(f"Running ty on project '{project.name}'")

        extra_args = project.paths
        cmd = [
            self.executable.as_posix(),
            "check",
            "--output-format=concise",
            "--python",
            str(project.venv_path),
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

        if result.returncode not in (0, 1):
            logging.error(
                f"ty failed with error code {result.returncode} for project '{project.name}' ... panic?"
            )

        with open(LOG_FILE, "a") as log_file:
            log_file.write(result.stdout)

        parser = DiagnosticsParser(
            repo_location=project.location,
            repo_commit=project.current_commit,
            repo_working_dir=project.root_directory,
        )
        diagnostics = parser.parse(result.stdout)

        return {
            "project": project.name,
            "project_location": project.location,
            "ty_commit": self.repository.head.commit.hexsha,
            "diagnostics": diagnostics,
        }

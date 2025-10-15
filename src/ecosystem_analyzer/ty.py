import logging
import os
import subprocess
import time
from pathlib import Path

from git import Commit, Repo

from .diagnostic import DiagnosticsParser
from .installed_project import InstalledProject
from .run_output import RunOutput


class Ty:
    def __init__(self, repository: Repo, profile: str = "dev") -> None:
        self.repository: Repo = repository
        self.working_dir: Path = Path(self.repository.working_dir)
        self.cargo_target_dir: Path = self.working_dir / "target"
        self.profile: str = profile

    def compile_for_commit(self, commit: str | Commit):
        # Checkout the commit
        logging.debug(f"Checking out ty commit '{commit}'")
        self.repository.git.checkout(commit)

        # Compile ty
        env = os.environ.copy()
        env["CARGO_TARGET_DIR"] = self.cargo_target_dir.as_posix()

        logging.info(f"Compiling ty ({self.profile})")
        cargo_cmd = ["cargo", "build", "--package", "ty", "--profile", self.profile]
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

        # Cargo uses "dev" as the profile name, but outputs to "debug" directory
        # For other profiles, the directory name matches the profile name
        target_dir = "debug" if self.profile == "dev" else self.profile
        self.executable = self.cargo_target_dir / target_dir / "ty"

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
        start_time = time.time()
        try:
            result = subprocess.run(
                cmd,
                cwd=project.root_directory,
                check=False,
                capture_output=True,
                text=True,
                timeout=30 if self.profile == "release" else 180,
            )

            execution_time = time.time() - start_time
            return_code = result.returncode

            if result.returncode not in (0, 1):
                logging.error(
                    f"ty failed with error code {result.returncode} for project '{project.name}' ... panic?"
                )
                # Don't trust execution time for abnormal exits
                execution_time = None

            parser = DiagnosticsParser(
                repo_location=project.location,
                repo_commit=project.current_commit,
                repo_working_dir=project.root_directory,
            )

            diagnostics = parser.parse(result.stdout)
        except subprocess.TimeoutExpired:
            diagnostics = []
            execution_time = None
            return_code = None

        return RunOutput(
            {
                "project": project.name,
                "project_location": project.location,
                "ty_commit": self.repository.head.commit.hexsha,
                "diagnostics": diagnostics,
                "time_s": execution_time,
                "return_code": return_code,
            }
        )

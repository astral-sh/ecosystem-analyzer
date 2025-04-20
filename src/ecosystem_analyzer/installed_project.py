import logging
import subprocess
import tempfile
from pathlib import Path

from git import Repo

from .config import LOG_FILE, PYTHON_VERSION


class InstalledProject:
    def __init__(self, project) -> None:
        self.project = project
        self.temp_dir = tempfile.TemporaryDirectory()

    def _clone(self) -> None:
        try:
            logging.info(f"Cloning {self.project.location} into {self.temp_dir.name}")
            Repo.clone_from(url=self.project.location, to_path=self.temp_dir.name)
        except Exception as e:
            logging.error(f"Error cloning repository: {e}")
            return

    def _install_dependencies(self) -> None:
        venv_cmd = ["uv", "venv", "--quiet", "--python", PYTHON_VERSION]
        logging.debug(f"Executing: {' '.join(venv_cmd)}")
        subprocess.run(venv_cmd, check=True, cwd=self.temp_dir.name)

        if self.project.deps:
            logging.info(f"Installing dependencies: {', '.join(self.project.deps)}")

            pip_cmd = ["uv", "pip", "install", "--python", PYTHON_VERSION, "--link-mode=copy", *self.project.deps]
            logging.debug(f"Executing: {' '.join(pip_cmd)}")
            subprocess.run(
                pip_cmd,
                check=True,
                cwd=self.temp_dir.name,
                capture_output=False,
            )
        else:
            logging.info("No dependencies to install")

    def install(self) -> None:
        self._clone()
        self._install_dependencies()

    def count_diagnostics(self, red_knot: Path) -> int:
        extra_args = self.project.paths if self.project.paths else []
        cmd = [
            red_knot.as_posix(),
            "check",
            "--output-format=concise",
            "--python",
            ".venv",
            *extra_args,
        ]
        logging.debug(f"Executing: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            cwd=self.temp_dir.name,
            check=False,
            capture_output=True,
            text=True,
        )

        # Append result.stdout to log file
        with open(LOG_FILE, "a") as log_file:
            log_file.write(result.stdout)

        return len(result.stdout.splitlines()) 

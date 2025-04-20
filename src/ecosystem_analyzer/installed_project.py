import logging
import subprocess
import tempfile
from pathlib import Path

from git import Repo
from mypy_primer.model import Project

from .config import PYTHON_VERSION


class InstalledProject:
    _repo: Repo

    def __init__(self, project: Project) -> None:
        self._project = project
        self._temp_dir = tempfile.TemporaryDirectory()

        self._clone()
        self._install_dependencies()

    @property
    def root_directory(self) -> Path:
        return Path(self._temp_dir.name)

    @property
    def paths(self) -> list[str]:
        return self._project.paths or []

    @property
    def name(self) -> str:
        return self._project.name_override or self._project.location.split("/")[-1]

    @property
    def location(self) -> str:
        return self._project.location

    @property
    def default_branch(self) -> str:
        return self._repo.active_branch.name

    def _clone(self) -> None:
        try:
            logging.info(f"Cloning {self._project.location} into {self._temp_dir.name}")
            self._repo = Repo.clone_from(
                url=self._project.location, to_path=self._temp_dir.name
            )
        except Exception as e:
            logging.error(f"Error cloning repository: {e}")
            return

    def _install_dependencies(self) -> None:
        venv_cmd = ["uv", "venv", "--quiet", "--python", PYTHON_VERSION]
        logging.debug(f"Executing: {' '.join(venv_cmd)}")
        subprocess.run(venv_cmd, check=True, cwd=self._temp_dir.name)

        if self._project.deps:
            logging.info(f"Installing dependencies: {', '.join(self._project.deps)}")

            pip_cmd = [
                "uv",
                "pip",
                "install",
                "--python",
                PYTHON_VERSION,
                "--link-mode=copy",
                *self._project.deps,
            ]
            logging.debug(f"Executing: {' '.join(pip_cmd)}")
            subprocess.run(
                pip_cmd,
                check=True,
                cwd=self._temp_dir.name,
                capture_output=False,
            )
        else:
            logging.info("No dependencies to install")

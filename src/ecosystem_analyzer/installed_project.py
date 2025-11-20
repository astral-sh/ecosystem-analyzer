import hashlib
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from git import Repo
from mypy_primer.model import Project

from .config import PYTHON_VERSION


def _get_cache_dir() -> Path:
    """Get the XDG cache directory for ecosystem-analyzer."""
    cache_home = os.environ.get("XDG_CACHE_HOME")
    if cache_home:
        cache_dir = Path(cache_home) / "ecosystem-analyzer"
    else:
        cache_dir = Path.home() / ".cache" / "ecosystem-analyzer"

    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _get_project_cache_path(project: Project) -> Path:
    """Get the cache path for a specific project."""
    # Use a hash of the location to create a unique directory name
    location_hash = hashlib.sha256(project.location.encode()).hexdigest()[:12]
    project_name = project.name_override or project.location.split("/")[-1]
    cache_dir = _get_cache_dir()
    return cache_dir / f"{project_name}_{location_hash}"


class InstalledProject:
    _repo: Repo

    def __init__(self, project: Project) -> None:
        self._project = project
        self._cache_path = _get_project_cache_path(project)
        self._temp_dir = tempfile.TemporaryDirectory()

        self._clone_or_update()
        self._install_dependencies()

    @property
    def root_directory(self) -> Path:
        return self._cache_path

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

    @property
    def venv_path(self) -> Path:
        return Path(self._temp_dir.name) / ".venv"

    @property
    def current_commit(self) -> str:
        return self._repo.head.commit.hexsha

    @property
    def ty_cmd(self) -> str | None:
        return self._project.ty_cmd

    def _clone_or_update(self) -> None:
        try:
            if self._cache_path.exists():
                logging.info(f"Using cached repository at {self._cache_path}")
                self._repo = Repo(self._cache_path)
                # Update the repository to latest
                logging.debug("Updating cached repository")
                self._repo.remote().fetch()
                self._repo.git.reset("--hard", "origin/HEAD")
                # Update submodules
                for submodule in self._repo.submodules:
                    submodule.update(recursive=True)
            else:
                logging.info(
                    f"Cloning {self._project.location} into {self._cache_path}"
                )
                self._repo = Repo.clone_from(
                    url=self._project.location,
                    to_path=self._cache_path,
                    recurse_submodules=True,
                )
        except Exception as e:
            logging.error(f"Error cloning/updating repository: {e}")
            return

    def _install_dependencies(self) -> None:
        # Create venv in temporary directory
        venv_cmd = ["uv", "venv", "--quiet", "--python", PYTHON_VERSION]
        logging.debug(f"Executing: {' '.join(venv_cmd)}")
        subprocess.run(venv_cmd, check=True, cwd=self._temp_dir.name)

        # Get the venv python path for installations
        venv_python = Path(self._temp_dir.name) / ".venv" / "bin" / "python"

        if self._project.install_cmd:
            logging.info(f"Running custom install command: {self._project.install_cmd}")

            # Use absolute path to venv python for install commands
            install_placeholder = f"uv pip install --python {venv_python}"
            install_cmd = self._project.install_cmd.format(install=install_placeholder)

            logging.debug(f"Executing: '{install_cmd}'")
            subprocess.run(
                install_cmd,
                shell=True,
                check=True,
                cwd=self._cache_path,  # Run in cached project directory
                capture_output=False,
            )
        elif self._project.deps:
            logging.info(f"Installing dependencies: {', '.join(self._project.deps)}")

            pip_cmd = [
                "uv",
                "pip",
                "install",
                "--python",
                str(venv_python),
                "--link-mode=copy",
                *self._project.deps,
            ]
            logging.debug(f"Executing: {' '.join(pip_cmd)}")
            subprocess.run(
                pip_cmd,
                check=True,
                cwd=self._cache_path,  # Run in cached project directory
                capture_output=False,
            )
        else:
            logging.info("No dependencies to install")

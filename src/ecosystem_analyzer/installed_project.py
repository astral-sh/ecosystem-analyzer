import datetime as dt
import hashlib
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from git import GitError, Repo
from mypy_primer.model import Project

from .config import PYTHON_VERSION

logger = logging.getLogger(__name__)


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


def validate_exclude_newer(value: str) -> dt.datetime:
    """Validate and parse an ISO 8601 timestamp for --exclude-newer.

    Returns a timezone-aware UTC datetime.

    Raises ValueError if the timestamp cannot be parsed or is missing timezone info.
    """
    try:
        res = dt.datetime.fromisoformat(value)
    except ValueError as e:
        raise ValueError(
            f"Invalid --exclude-newer timestamp: {value!r}. "
            f"Expected an ISO 8601 timestamp (e.g. '2026-04-09T10:00:00Z')."
        ) from e

    if res.tzinfo is None:
        raise ValueError(
            f"--exclude-newer timestamp {value!r} is missing timezone info. "
            f"Use a UTC timestamp like '2026-04-09T10:00:00Z'."
        )

    return res.astimezone(dt.UTC)


class InstalledProject:
    _repo: Repo

    def __init__(self, project: Project, exclude_newer: str | None = None) -> None:
        self._project = project
        self._exclude_newer = exclude_newer
        self._cache_path = _get_project_cache_path(project)
        self._temp_dir = tempfile.TemporaryDirectory()

        if exclude_newer is not None:
            validate_exclude_newer(exclude_newer)

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
                logger.info(f"Using cached repository at {self._cache_path}")
                self._repo = Repo(self._cache_path)
                # Update the repository to latest
                logger.debug("Updating cached repository")
                self._repo.remote().fetch(depth=1)
                self._repo.git.reset("--hard", "origin/HEAD")
                # Update submodules
                for submodule in self._repo.submodules:
                    submodule.update(
                        recursive=True,
                        clone_multi_options=["--depth", "1"],
                    )
            else:
                logger.info(f"Cloning {self._project.location} into {self._cache_path}")
                self._repo = Repo.clone_from(
                    url=self._project.location,
                    to_path=self._cache_path,
                    recurse_submodules=True,
                    depth=1,
                )
        except GitError as e:
            logger.error(f"Error cloning/updating repository: {e}")
            return

        if self._exclude_newer is not None:
            self._pin_to_timestamp()

    def _pin_to_timestamp(self) -> None:
        """Checkout the latest commit at or before the exclude-newer timestamp."""
        assert self._exclude_newer is not None

        cutoff = validate_exclude_newer(self._exclude_newer)
        head_date = self._repo.head.commit.committed_datetime.astimezone(dt.UTC)

        if head_date <= cutoff:
            logger.debug(
                f"'{self.name}': HEAD ({head_date.isoformat()}) is already "
                f"at or before {self._exclude_newer}"
            )
            return

        logger.info(
            f"'{self.name}': HEAD ({head_date.isoformat()}) is newer than "
            f"{self._exclude_newer}, searching for older commit"
        )

        # Deepen the shallow clone to find a commit before the cutoff
        try:
            self._repo.git.fetch("--deepen", "20")
        except GitError:
            logger.warning(f"'{self.name}': failed to deepen clone, using HEAD as-is")
            return

        try:
            commit_hash = self._repo.git.rev_list(
                "HEAD", "--before", self._exclude_newer, "-1"
            )
        except GitError:
            logger.warning(
                f"'{self.name}': failed to find commit before "
                f"{self._exclude_newer}, using HEAD as-is"
            )
            return

        if not commit_hash.strip():
            logger.warning(
                f"'{self.name}': no commit found before "
                f"{self._exclude_newer}, using HEAD as-is"
            )
            return

        logger.info(
            f"'{self.name}': checking out {commit_hash[:12]} "
            f"(latest before {self._exclude_newer})"
        )
        self._repo.git.checkout(commit_hash)

    def _install_dependencies(self) -> None:
        # Create venv in temporary directory
        venv_cmd = ["uv", "venv", "--quiet", "--python", PYTHON_VERSION]
        logger.debug(f"Executing: {' '.join(venv_cmd)}")
        subprocess.run(venv_cmd, check=True, cwd=self._temp_dir.name)

        # Get the venv python path for installations
        venv_python = Path(self._temp_dir.name) / ".venv" / "bin" / "python"

        if self._project.install_cmd:
            logger.info(f"Running custom install command: {self._project.install_cmd}")

            # Use absolute path to venv python for install commands
            install_placeholder = f"uv pip install --python {venv_python}"
            if self._exclude_newer:
                install_placeholder += f" --exclude-newer {self._exclude_newer}"
            install_cmd = self._project.install_cmd.format(install=install_placeholder)

            logger.debug(f"Executing: '{install_cmd}'")
            subprocess.run(
                install_cmd,
                shell=True,
                check=True,
                cwd=self._cache_path,  # Run in cached project directory
                capture_output=False,
            )
        elif self._project.deps:
            logger.info(f"Installing dependencies: {', '.join(self._project.deps)}")

            exclude_newer_args = (
                ["--exclude-newer", self._exclude_newer] if self._exclude_newer else []
            )
            pip_cmd = [
                "uv",
                "pip",
                "install",
                "--python",
                str(venv_python),
                "--link-mode=copy",
                *exclude_newer_args,
                *self._project.deps,
            ]
            logger.debug(f"Executing: {' '.join(pip_cmd)}")
            subprocess.run(
                pip_cmd,
                check=True,
                cwd=self._cache_path,  # Run in cached project directory
                capture_output=False,
            )
        else:
            logger.info("No dependencies to install")

"""Install ecosystem projects and coordinate ty analysis runs."""

import json
import logging
import time
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path

from mypy_primer.model import Project
from mypy_primer.projects import get_projects

from .installed_project import InstalledProject
from .schema import RunData, RunOutput
from .ty import Ty

logger = logging.getLogger(__name__)


def get_ecosystem_projects() -> dict[str, Project]:
    """Index the mypy_primer ecosystem projects by their configured names."""

    projects: dict[str, Project] = {}
    for project in get_projects():
        project_name = project.name_override or project.location.split("/")[-1]

        projects[project_name] = project

    return projects


class Manager:
    """Coordinate project installation, ty builds, and ecosystem analysis."""

    _project_names: list[str]
    _installed_projects: list[InstalledProject]

    _ty: Ty

    def __init__(
        self,
        *,
        ty_repo: Path | None = None,
        target_dir: Path | None,
        project_names: list[str],
        profile: str = "dev",
        flaky_runs: int = 1,
        flaky_projects: set[str] | None = None,
        exclude_newer: str | None = None,
        ecosystem_projects: dict[str, Project] | None = None,
    ) -> None:
        self._installed_projects = []
        self._ty = Ty(ty_repo, target_dir, profile=profile)
        self._flaky_runs = flaky_runs
        self._flaky_projects = flaky_projects or set()
        self._exclude_newer = exclude_newer

        self._ecosystem_projects = (
            ecosystem_projects
            if ecosystem_projects is not None
            else get_ecosystem_projects()
        )

        unavailable_projects = set(project_names) - set(self._ecosystem_projects.keys())
        if unavailable_projects:
            logger.warning(
                f'Project(s) "{", ".join(sorted(unavailable_projects))}" not found in available projects. Skipping.'
            )

        # Filter out unavailable projects and continue with available ones
        self._project_names = [
            name for name in project_names if name in self._ecosystem_projects
        ]

        if not self._project_names:
            raise RuntimeError("No valid projects found to analyze.")

        # Start installation before building ty, and expose each project as it
        # becomes ready so prebuilt comparisons can overlap the remaining work.
        max_workers = min(len(self._project_names), 8)
        self._install_executor = ThreadPoolExecutor(max_workers=max_workers)
        self._install_futures: dict[Future[InstalledProject], str] = {
            self._install_executor.submit(self._install_project, name): name
            for name in self._project_names
        }

    def _install_project(self, name: str) -> InstalledProject:
        logger.info(f"Processing project: {name}")
        return InstalledProject(
            self._ecosystem_projects[name], exclude_newer=self._exclude_newer
        )

    def _iter_projects(self) -> Iterator[InstalledProject]:
        """Yield cached projects, then installations as they complete."""
        yield from self._installed_projects
        if not self._install_futures:
            return
        pending = as_completed(self._install_futures)
        wait_time = 0.0
        try:
            while self._install_futures:
                wait_start = time.monotonic()
                future = next(pending)
                wait_time += time.monotonic() - wait_start
                name = self._install_futures[future]
                try:
                    project = future.result()
                except Exception:
                    logger.exception(f"Failed to install project {name}")
                    raise
                del self._install_futures[future]
                self._installed_projects.append(project)
                yield project
        finally:
            logger.info(f"Waited {wait_time:.1f}s for project installation")

    def _ensure_installed(self) -> None:
        """Block until project installation is complete."""
        if not self._install_futures:
            return
        try:
            for _ in self._iter_projects():
                pass
        finally:
            self._install_executor.shutdown(wait=True, cancel_futures=True)

    def build(self, commit: str) -> None:
        """Build ty for a commit. Can be called while projects are still installing."""
        self._ty.compile_for_commit(commit)

    def use_prebuilt(self, binary_path: Path, commit_sha: str) -> None:
        """Use a pre-built ty binary instead of building from source."""
        self._ty.use_prebuilt(binary_path, commit_sha)

    def run_for_commit(self, commit: str) -> list[RunOutput]:
        """Build ty for a commit and run it on the installed projects.

        The build runs first and can overlap with background project
        installation. We only block on installation before running ty.
        """
        self.build(commit)
        self._ensure_installed()
        return self._run_projects()

    def run_projects(self) -> list[RunOutput]:
        """Run the current ty build on the installed projects."""
        self._ensure_installed()
        return self._run_projects()

    def _run_projects(self) -> list[RunOutput]:
        return [
            self._run_project(self._ty, project) for project in self._installed_projects
        ]

    def _run_project(self, ty: Ty, project: InstalledProject) -> RunOutput:
        if self._flaky_runs > 1 and (
            not self._flaky_projects or project.name in self._flaky_projects
        ):
            return ty.run_on_project_multiple(project, self._flaky_runs)
        return ty.run_on_project(project)

    def run_prebuilt_diff(
        self,
        *,
        old_binary: Path,
        old_commit: str,
        new_binary: Path,
        new_commit: str,
    ) -> tuple[list[RunOutput], list[RunOutput]]:
        """Compare both binaries on each project as its installation finishes.

        The two revisions run consecutively on the same prepared project. Other
        installations may still be running, but checker processes never overlap.
        """
        old_ty = Ty(profile=self._ty.profile)
        old_ty.use_prebuilt(old_binary, old_commit)
        new_ty = Ty(profile=self._ty.profile)
        new_ty.use_prebuilt(new_binary, new_commit)
        old_outputs = []
        new_outputs = []
        try:
            for project in self._iter_projects():
                old_outputs.append(self._run_project(old_ty, project))
                new_outputs.append(self._run_project(new_ty, project))
        finally:
            self._install_executor.shutdown(wait=True, cancel_futures=True)
        return old_outputs, new_outputs

    def write_run_outputs(
        self, run_outputs: list[RunOutput], output_path: str | Path
    ) -> None:
        """Write project run outputs to a formatted JSON report."""

        output_path = Path(output_path)
        with output_path.open("w") as json_file:
            json.dump(RunData(outputs=run_outputs), json_file, indent=4)
        logger.info(f"Output written to {output_path}")

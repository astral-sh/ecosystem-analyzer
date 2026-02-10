import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from git import Commit, Repo
from mypy_primer.model import Project
from mypy_primer.projects import get_projects

from .installed_project import InstalledProject
from .run_output import RunOutput
from .ty import Ty


def _get_ecosystem_projects() -> dict[str, Project]:
    projects: dict[str, Project] = {}
    for project in get_projects():
        project_name = (
            project.name_override
            if project.name_override
            else project.location.split("/")[-1]
        )

        projects[project_name] = project

    return projects


class Manager:
    _project_names: list[str]
    _installed_projects: list[InstalledProject] = []
    _active_projects: list[InstalledProject] = []

    _ty: Ty

    def __init__(
        self,
        *,
        ty_repo: Repo,
        target_dir: Path | None,
        project_names: list[str],
        profile: str = "dev",
        flaky_runs: int = 1,
        flaky_projects: set[str] | None = None,
    ) -> None:
        self._ty = Ty(ty_repo, target_dir, profile=profile)
        self._flaky_runs = flaky_runs
        self._flaky_projects = flaky_projects or set()

        self._ecosystem_projects = _get_ecosystem_projects()

        unavailable_projects = set(project_names) - set(self._ecosystem_projects.keys())
        if unavailable_projects:
            logging.warning(
                f'Project(s) "{", ".join(sorted(unavailable_projects))}" not found in available projects. Skipping.'
            )

        # Filter out unavailable projects and continue with available ones
        self._project_names = [
            name for name in project_names if name in self._ecosystem_projects
        ]

        if not self._project_names:
            raise RuntimeError("No valid projects found to analyze.")

        self._install_projects()
        # By default, activate all installed projects
        self._active_projects = self._installed_projects.copy()

    def _install_projects(self) -> None:
        def install_single_project(project_name: str) -> InstalledProject:
            logging.info(f"Processing project: {project_name}")
            project = self._ecosystem_projects[project_name]
            return InstalledProject(project)

        max_workers = min(len(self._project_names), 8)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all installation tasks
            future_to_project = {
                executor.submit(install_single_project, project_name): project_name
                for project_name in self._project_names
            }

            # Collect results as they complete
            for future in as_completed(future_to_project):
                project_name = future_to_project[future]
                try:
                    installed_project = future.result()
                    self._installed_projects.append(installed_project)
                    logging.debug(f"Successfully installed project: {project_name}")
                except Exception as e:
                    logging.error(f"Failed to install project {project_name}: {e}")
                    raise

    def activate(self, project_names: list[str]) -> None:
        """Activate a subset of installed projects for running."""
        # Validate that all requested projects are installed
        installed_project_names = {project.name for project in self._installed_projects}

        unavailable_projects = set(project_names) - installed_project_names
        if unavailable_projects:
            logging.warning(
                f'Project(s) "{", ".join(sorted(unavailable_projects))}" not found in installed projects. Skipping.'
            )

        # Filter installed projects to only include the requested ones that are available
        available_project_names = [
            name for name in project_names if name in installed_project_names
        ]
        self._active_projects = [
            project
            for project in self._installed_projects
            if project.name in available_project_names
        ]

    def run_for_commit(self, commit: str | Commit) -> list[RunOutput]:
        self._ty.compile_for_commit(commit)

        run_outputs = []
        for project in self._active_projects:
            n = (
                self._flaky_runs
                if (
                    self._flaky_runs > 1
                    and (
                        not self._flaky_projects or project.name in self._flaky_projects
                    )
                )
                else 1
            )
            if n > 1:
                output = self._ty.run_on_project_multiple(project, n)
            else:
                output = self._ty.run_on_project(project)
            run_outputs.append(output)

        return run_outputs

    def write_run_outputs(
        self, run_outputs: list[RunOutput], output_path: str | Path
    ) -> None:
        output_path = Path(output_path)
        with output_path.open("w") as json_file:
            json.dump({"outputs": run_outputs}, json_file, indent=4)
        logging.info(f"Output written to {output_path}")

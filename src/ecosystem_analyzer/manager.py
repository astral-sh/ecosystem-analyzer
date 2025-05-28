import json
import logging
from pathlib import Path

from git import Repo
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

    _ty: Ty

    def __init__(
        self,
        *,
        ty_repo: Repo,
        project_names: list[str],
    ) -> None:
        self._ty = Ty(ty_repo)

        self._ecosystem_projects = _get_ecosystem_projects()

        unavailable_projects = set(project_names) - set(self._ecosystem_projects.keys())
        if unavailable_projects:
            raise RuntimeError(
                f"Projects {', '.join(unavailable_projects)} not found in available projects. "
            )

        self._project_names = project_names
        self._install_projects()

    def _install_projects(self) -> None:
        for project_name in self._project_names:
            logging.info(f"Processing project: {project_name}")

            project = self._ecosystem_projects[project_name]
            self._installed_projects.append(InstalledProject(project))

    def run_for_commit(self, commit: str) -> list[RunOutput]:
        self._ty.compile_for_commit(commit)

        run_outputs = []
        for project in self._installed_projects:
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

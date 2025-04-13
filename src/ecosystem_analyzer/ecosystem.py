from dataclasses import dataclass
from pathlib import Path

import tomllib


@dataclass
class Project:
    name: str
    location: str
    deps: list[str]
    paths: list[str]

    @classmethod
    def from_toml(cls, name: str, data: dict) -> "Project":
        return cls(
            name=name,
            location=data["location"],
            deps=data.get("deps", []),
            paths=data.get("paths", []),
        )


def load_ecosystem(toml_path: str = "ecosystem.toml") -> list[Project]:
    """Load projects from ecosystem.toml file."""
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)

    projects = []
    for name, project_data in data["projects"].items():
        projects.append(Project.from_toml(name, project_data))

    return projects 

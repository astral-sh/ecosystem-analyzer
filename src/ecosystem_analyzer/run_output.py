from typing import TypedDict

from .diagnostic import Diagnostic


class RunOutput(TypedDict):
    project: str
    project_location: str
    ty_commit: str
    diagnostics: list[Diagnostic]
    time_s: float | None
    return_code: int | None

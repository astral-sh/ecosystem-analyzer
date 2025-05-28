from typing import TypedDict

from .diagnostic import Diagnostic


class RunOutput(TypedDict):
    project: str
    ty_commit: str
    diagnostics: list[Diagnostic]

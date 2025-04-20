import json
import logging
from pathlib import Path
from typing import TypedDict

from .diagnostic import Diagnostic


class RunOutput(TypedDict):
    project: str
    red_knot_commit: str
    diagnostics: list[Diagnostic]

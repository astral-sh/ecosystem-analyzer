import json
import logging
from pathlib import Path
from typing import TypedDict

from .diagnostic import Diagnostic


class RunOutput(TypedDict):
    project: str
    red_knot_commit: str
    diagnostics_count: int
    diagnostics: list[Diagnostic]


def write_run_output(statistics: RunOutput, filename: Path) -> None:
    # Write to JSON file
    with filename.open("w") as json_file:
        json.dump(statistics, json_file, indent=4)
    logging.info(f"Statistics written to {filename}")

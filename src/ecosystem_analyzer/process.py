"""Shared subprocess helpers."""

import subprocess
from pathlib import Path


def run(*args: str, cwd: Path) -> str:
    """Run a command, returning its stdout unchanged and raising on failure."""
    return subprocess.run(
        list(args), cwd=cwd, check=True, capture_output=True, text=True
    ).stdout
